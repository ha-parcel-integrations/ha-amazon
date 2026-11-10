"""Coordinator for the Amazon parcel tracker integration.

Fetching and event firing only — the parcel mapping lives in :mod:`.parcels`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_INCLUDE_HISTORY,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    HOT_LOOKAHEAD_HOURS,
    IDLE_INTERVAL_MINUTES,
    MID_INTERVAL_MINUTES,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
    STAGGER_MINUTES,
    ParcelStatus,
)
from ..handoff import HandOff
from .client import AmazonClient
from .errors import AmazonApiError, AmazonAuthError
from .parcels import apply_delivered_filter, normalize_parcel, sort_parcels_by_ts

_LOGGER = logging.getLogger(__name__)

# Base for the 429 backoff when the carrier's response carries no
# ``Retry-After`` of its own: ``BACKOFF_BASE_SECONDS * 2**consecutive_429``,
# capped at ``BACKOFF_CAP_SECONDS``.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600

BUSINESS_LINES_VERSION = 1
PHOTO_TIMEOUT_SECONDS = 30


@dataclass
class DeliveryPhoto:
    """A parcel's delivery photo; the link is never published."""

    url: str
    content: bytes | None = None
    content_type: str | None = None


def photo_unique_id(entry_id: str, barcode: str) -> str:
    """Return the unique id of a parcel's delivery-photo entity."""
    return f"{entry_id}_delivery_photo_{barcode}"


def business_lines_store(hass: HomeAssistant, entry_id: str) -> Store[dict]:
    """Where an entry keeps its Business lines across restarts."""
    return Store(hass, BUSINESS_LINES_VERSION, f"{DOMAIN}.business_lines.{entry_id}")


def _shipment(parcel: dict) -> tuple[Any, Any, Any]:
    """Return the Amazon shipment a parcel is, whatever its barcode."""
    raw = parcel["raw"]
    return (raw.get("order_id"), raw.get("shipment_id"), raw.get("package_id"))


def _identities(parcel: dict) -> list[tuple[Any, ...]]:
    """Every key the parcel's previous stand-in may be remembered under.

    A line gains its shipment id only once it ships, which changes the
    shipment key; its order line id stays the same.
    """
    raw = parcel["raw"]
    lines = [(raw.get("order_id"), line) for line in raw.get("line_item_ids") or []]
    return [_shipment(parcel), *lines]


def _stagger_minutes(entry_id: str) -> int:
    """Deterministic per-install offset, stable across restarts."""
    digest = hashlib.sha256(entry_id.encode()).hexdigest()
    return int(digest, 16) % STAGGER_MINUTES


def _in_quiet_window(moment: datetime) -> bool:
    """Whether ``moment`` (local time) falls in the no-polling window."""
    return QUIET_WINDOW_START_HOUR <= moment.hour < QUIET_WINDOW_END_HOUR


def _next_anchor(now: datetime) -> datetime:
    """Return the next of the two daily anchors (00:00 / 06:00 local)."""
    six_today = now.replace(
        hour=QUIET_WINDOW_END_HOUR, minute=0, second=0, microsecond=0
    )
    if now < six_today:
        return six_today
    midnight_tomorrow = (now + timedelta(days=1)).replace(
        hour=QUIET_WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    return midnight_tomorrow


def _hottest_tier_minutes(active_parcels: list[dict], now: datetime) -> int:
    """Tier for the account-based model.

    Never returns ``None`` — the account is the discovery path for new
    shipments, so polling only slows down (idle tier) and never stops.
    """
    if not active_parcels:
        return IDLE_INTERVAL_MINUTES
    for parcel in active_parcels:
        if parcel["status"] != ParcelStatus.OUT_FOR_DELIVERY:
            continue
        planned_from = parcel.get("planned_from")
        if not planned_from:
            return HOT_INTERVAL_MINUTES
        planned_dt = dt_util.parse_datetime(planned_from)
        if planned_dt is None:
            return HOT_INTERVAL_MINUTES
        if dt_util.as_utc(now) >= dt_util.as_utc(planned_dt) - timedelta(
            hours=HOT_LOOKAHEAD_HOURS
        ):
            return HOT_INTERVAL_MINUTES

    return MID_INTERVAL_MINUTES


def _next_update_interval(now: datetime, tier_minutes: int, entry_id: str) -> timedelta:
    """Turn a tier into the coordinator's next ``update_interval``.

    Clamp the naive next-due time forward to the next anchor whenever it
    would land inside the quiet window — including when ``now`` itself is
    already inside it (an anchor poll computing its own follow-up).
    """
    if _in_quiet_window(now):
        return _next_anchor(now) - now

    stagger = timedelta(minutes=_stagger_minutes(entry_id))
    candidate = now + timedelta(minutes=tier_minutes) + stagger
    if _in_quiet_window(candidate):
        return _next_anchor(now) - now
    return candidate - now


class AmazonCoordinator(DataUpdateCoordinator[list[dict]]):
    """Polls the account's parcel list and publishes the canonical lists.

    ``coordinator.data`` is the active (not-yet-delivered) parcels,
    ``self.delivered`` the rest.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: AmazonClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Passing config_entry makes self.config_entry available on the
            # base class, which every helper below relies on.
            config_entry=entry,
            name=DOMAIN,
            # Recomputed at the end of every refresh (Section 2.2's tiering) —
            # start with the hot cadence so the very first poll, right after
            # setup, happens promptly regardless of what it finds.
            update_interval=timedelta(minutes=HOT_INTERVAL_MINUTES),
        )
        self._client = client
        self.handoff = HandOff(hass, entry.entry_id, entry.options)
        # A delivered Business card names no shipment; what the client learnt
        # in flight must outlive a restart or the parcel changes barcode.
        self._business_store = business_lines_store(hass, entry.entry_id)
        self._business_saved: list[list[str]] | None = None
        # Every parcel of the last poll, handed-off ones included, so the
        # hand-off can run again once Home Assistant has started.
        self._all_parcels: list[dict] = []
        self.delivered: list[dict] = []
        self.unresolved: list[dict] = []
        # Barcode -> delivery photo, for the parcels the delivered list shows.
        self.photos: dict[str, DeliveryPhoto] = {}
        self._photo_entity_sync: Callable[[], Awaitable[None]] | None = None
        # Consecutive 429 responses, for the exponential backoff in Section 3.
        # Reset to 0 on any success.
        self._consecutive_429 = 0
        # Last tier computed by _hottest_tier_minutes — surfaced in
        # diagnostics.
        self._current_tier_minutes: int | None = None
        # barcode -> last seen ParcelStatus / (planned_from, planned_to).
        # ``None`` on the first refresh so events are suppressed for parcels
        # that already existed when the integration started — otherwise every
        # restart would flood users with "registered" notifications.
        self._known_state: dict[str, ParcelStatus] | None = None
        self._known_delivery_times: dict[str, tuple[str | None, str | None]] | None = (
            None
        )
        # Shipment or order line -> stand-in barcode of the last poll, so a
        # parcel whose stand-in turns into its tracking id is not announced a
        # second time.
        self._stand_ins: dict[tuple[Any, ...], str] = {}
        # Cached device id, attached to every fired event so device-trigger
        # automations can filter to this account's device.
        self._cached_device_id: str | None = None
        # Timestamp of the last successful poll (diagnostic sensor).
        self.last_success_time: datetime | None = None

    @property
    def current_tier_minutes(self) -> int | None:
        """Tier minutes computed on the last refresh (diagnostics only)."""
        return self._current_tier_minutes

    @property
    def delivered_codes(self) -> set[str]:
        """Always empty — nothing per-parcel to skip in the account model."""
        return set()

    @property
    def listing_complete(self) -> bool:
        """Whether every listed shipment has been read since the last restart."""
        return self._client.listing_complete

    @callback
    def async_set_photo_entity_sync(
        self, sync: Callable[[], Awaitable[None]]
    ) -> CALLBACK_TYPE:
        """Register what brings the image entities in line with ``photos``."""
        self._photo_entity_sync = sync

        @callback
        def _remove() -> None:
            self._photo_entity_sync = None

        return _remove

    async def async_load_photo(self, barcode: str) -> DeliveryPhoto | None:
        """Return a parcel's photo, downloading it if that has not worked yet."""
        photo = self.photos.get(barcode)
        if photo is None or photo.content is not None:
            return photo
        try:
            async with asyncio.timeout(PHOTO_TIMEOUT_SECONDS):
                (
                    photo.content,
                    photo.content_type,
                ) = await self._client.async_fetch_photo(photo.url)
        except (aiohttp.ClientError, TimeoutError, AmazonApiError) as err:
            # Only the type: a client error's text carries the signed link.
            _LOGGER.debug("Could not fetch a delivery photo: %s", type(err).__name__)
        return photo

    async def _async_sync_photos(self) -> None:
        """Have every delivered parcel's photo in place before events fire.

        An automation triggered by the delivered event loads the image right
        away. The photo is downloaded straight away too: the link dies a few
        days after it was read, and a delivered parcel is not read again.
        """
        photos: dict[str, DeliveryPhoto] = {}
        for parcel in self.delivered:
            barcode = parcel.get("barcode")
            url = self._client.delivery_photo_url(_shipment(parcel))
            if barcode and url:
                known = self.photos.get(barcode)
                photos[barcode] = (
                    known if known and known.content else DeliveryPhoto(url)
                )
        self.photos = photos
        for barcode in photos:
            await self.async_load_photo(barcode)
        if self._photo_entity_sync is not None:
            await self._photo_entity_sync()

        registry = er.async_get(self.hass)
        for parcel in self.delivered:
            barcode = parcel.get("barcode")
            if barcode not in photos:
                continue
            parcel["raw"] = {
                **parcel["raw"],
                "delivery_photo_entity_id": registry.async_get_entity_id(
                    "image",
                    DOMAIN,
                    photo_unique_id(self.config_entry.entry_id, barcode),
                ),
            }

    def _device_id(self) -> str | None:
        """Resolve (and cache) this entry's device id for event payloads."""
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(
                dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            ),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY)
        )

    async def _async_update_data(self) -> list[dict]:
        try:
            return await self._async_poll()
        except Exception:
            # The base class keeps the last interval after a failure, which
            # after an overnight poll is the whole quiet window.
            now = dt_util.now()
            self.update_interval = _next_update_interval(
                now,
                _hottest_tier_minutes(self.data or [], now),
                self.config_entry.entry_id,
            )
            raise

    async def _async_poll(self) -> list[dict]:
        """Fetch the account's parcels and split into active vs delivered.

        ``aiohttp.ClientError`` and a non-429 ``AmazonApiError`` are
        deliberately not caught — ``DataUpdateCoordinator`` turns those into
        a failed update and the wrapper above reschedules it. An expired session needs
        special handling, because retrying it forever would never recover; a
        429 needs its own handling too, for the Section 3 backoff.
        """
        if self._business_saved is None:
            stored = await self._business_store.async_load() or {}
            self._business_saved = list(stored.get("lines", []))
            self._client.restore_business_lines(self._business_saved)
        try:
            raws = await self._client.async_get_parcels()
        except AmazonAuthError as err:
            raise ConfigEntryAuthFailed("Amazon session expired") from err
        except AmazonApiError as err:
            if err.status_code not in (429, 503):
                raise
            self._consecutive_429 += 1
            retry_after = err.retry_after or min(
                BACKOFF_BASE_SECONDS * 2**self._consecutive_429, BACKOFF_CAP_SECONDS
            )
            raise UpdateFailed(
                f"Amazon rate-limited (HTTP {err.status_code})", retry_after=retry_after
            ) from err
        self._consecutive_429 = 0
        if (lines := self._client.business_lines) != self._business_saved:
            await self._business_store.async_save({"lines": lines})
            self._business_saved = lines

        include_history = self._include_history
        self._all_parcels = [
            normalize_parcel(raw, include_history=include_history) for raw in raws
        ]
        self.last_success_time = datetime.now(timezone.utc)
        return await self._async_publish()

    async def async_hand_off_after_start(self) -> None:
        """Run the hand-off on the last poll once Home Assistant has started."""
        self.async_set_updated_data(await self._async_publish())

    async def _async_publish(self) -> list[dict]:
        """Split the last poll into the lists the entities show."""
        normalized = await self.handoff.async_process(self._all_parcels)
        # A line with no recognised status and no tracking id is not known to
        # be a parcel on its way, so it must not count as incoming.
        self.unresolved = [
            parcel
            for parcel in normalized
            if parcel["status"] is ParcelStatus.UNKNOWN
            and not parcel["raw"].get("tracking_id")
        ]
        normalized = [p for p in normalized if p not in self.unresolved]
        active = [parcel for parcel in normalized if not parcel["delivered"]]
        delivered = [parcel for parcel in normalized if parcel["delivered"]]

        delivered_sorted = sort_parcels_by_ts(
            delivered, "delivered_at", descending=True
        )
        self.delivered = apply_delivered_filter(delivered_sorted, self.config_entry)
        normalized_active = sort_parcels_by_ts(active, "planned_from")
        await self._async_sync_photos()

        # Events and remembered state cover every parcel; the retention option
        # only limits what the delivered sensor shows.
        incoming = normalized_active + delivered_sorted
        self._carry_known_state(incoming)
        self._fire_change_events(incoming)
        self._stand_ins = {
            identity: parcel["barcode"]
            for parcel in incoming
            if parcel.get("barcode")
            and parcel["raw"].get("barcode_source") == "shipment_key"
            for identity in _identities(parcel)
        }
        self._known_state = {
            parcel["barcode"]: parcel["status"]
            for parcel in incoming
            if parcel.get("barcode")
        }
        self._known_delivery_times = {
            parcel["barcode"]: (parcel.get("planned_from"), parcel.get("planned_to"))
            for parcel in incoming
            if parcel.get("barcode")
        }

        now = dt_util.now()
        self._current_tier_minutes = _hottest_tier_minutes(normalized_active, now)
        self.update_interval = _next_update_interval(
            now, self._current_tier_minutes, self.config_entry.entry_id
        )
        return normalized_active

    def _carry_known_state(self, parcels: list[dict]) -> None:
        """Treat a barcode as known when it is not really a new parcel.

        A stand-in that became the tracking id keeps its last status. Until
        the capped fan-out has read every shipment once (after a restart it
        takes a few polls), a barcode turning up is one that existed before.
        """
        if self._known_state is None or self._known_delivery_times is None:
            return
        present = {parcel.get("barcode") for parcel in parcels}
        complete = self._client.listing_complete
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode in self._known_state:
                continue
            old = next(
                (
                    stand_in
                    for identity in _identities(parcel)
                    if (stand_in := self._stand_ins.get(identity)) is not None
                    and stand_in not in present
                    and stand_in in self._known_state
                ),
                None,
            )
            if old is not None:
                self._known_state[barcode] = self._known_state[old]
                if old in self._known_delivery_times:
                    self._known_delivery_times[barcode] = self._known_delivery_times[
                        old
                    ]
            elif not complete:
                self._known_state[barcode] = parcel["status"]
                self._known_delivery_times[barcode] = (
                    parcel.get("planned_from"),
                    parcel.get("planned_to"),
                )

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire registered / status-changed / delivered / delivery-time events.

        Silent on the very first refresh — we cannot know which parcels are
        genuinely new versus already present before HA started.

        The event contract, identical across the suite:

        * every payload is the full normalised parcel plus ``device_id``;
        * the hop **to** ``delivered`` fires only ``_parcel_delivered``, never
          also ``_parcel_status_changed``;
        * a barcode first seen already-delivered fires nothing;
        * ``registered`` only fires for a new, not-yet-delivered barcode;
        * an ETA going ``value → null`` is intentionally silent — the carrier
          just lost the window, which is not worth waking someone up for.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue

            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

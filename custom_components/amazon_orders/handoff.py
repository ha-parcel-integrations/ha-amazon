"""Hand parcels over to the suite's carrier integrations.

Amazon is a shop: it knows what was ordered, the carrier knows where the parcel
is. Handing a parcel to the carrier's integration gives the user its delivery
window, pickup point and full history. Whatever the carrier does not accept
simply stays listed here.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HAND_OFF,
    CONF_TRACKING_CODE,
    DEFAULT_HAND_OFF,
    DOCS_CARRIERS_URL,
    DOMAIN,
    HAND_OFF_CARRIERS,
    SERVICE_TRACK_PARCEL,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# The order list is incomplete after a restart (the fan-out is capped), so a
# missing code is only forgotten once it is old enough to be truly gone.
FORGET_AFTER = timedelta(days=30)


def issue_id(carrier_domain: str) -> str:
    """One Repair issue per carrier domain, shared by every Amazon entry."""
    return f"carrier_not_set_up_{carrier_domain}"


def _hand_off_target(parcel: dict[str, Any]) -> str | None:
    """Return the carrier domain this parcel belongs to, else ``None``."""
    raw = parcel["raw"]
    if parcel.get("carrier") == "Amazon":
        return None
    # A stand-in barcode is not something a carrier can look up.
    if raw.get("barcode_source") != "tracking_id" or not parcel.get("barcode"):
        return None
    target = HAND_OFF_CARRIERS.get(raw.get("carrier_code"))
    return target[0] if target else None


class HandOff:
    """Per-entry hand-off state: what was handed over, and the calls to do it."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: Any) -> None:
        """Remember the entry; the stored state is read on first use."""
        self._hass = hass
        self._entry_id = entry_id
        self._options = options
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.hand_off.{entry_id}"
        )
        self._loaded = False
        # code -> {"domain": carrier domain, "since": ISO time of the hand-off}
        self._handed: dict[str, dict[str, str]] = {}
        # Codes that already failed this session; retried only after a reload.
        self._failed: set[str] = set()

    @property
    def _started(self) -> bool:
        """Whether Home Assistant is done starting (``is_running`` is true earlier)."""
        return self._hass.state is CoreState.running

    @property
    def enabled(self) -> bool:
        """Whether the user wants parcels handed off."""
        return bool(self._options.get(CONF_HAND_OFF, DEFAULT_HAND_OFF))

    async def _load(self) -> None:
        if self._loaded:
            return
        stored = await self._store.async_load() or {}
        self._handed = dict(stored.get("handed", {}))
        self._loaded = True

    async def _save(self) -> None:
        await self._store.async_save({"handed": self._handed})

    async def async_remove(self) -> None:
        """Forget the stored state when the entry is removed."""
        await self._store.async_remove()

    async def _track(self, domain: str, code: str) -> bool:
        """Hand a code to the carrier; ``False`` when the carrier refused it."""
        try:
            await self._hass.services.async_call(
                domain, SERVICE_TRACK_PARCEL, {CONF_TRACKING_CODE: code}, blocking=True
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "The %s integration did not accept tracking code %s (%s)."
                " Add it there by hand if you want it tracked by %s; it stays"
                " listed here until then.",
                domain,
                code,
                err,
                domain,
            )
            return False
        return True

    def _sync_issues(self, needed: set[str]) -> None:
        """Raise an issue for a missing carrier; clear it once the carrier is there.

        An issue is never cleared because its parcels went away: a user who
        ignored it would be nagged again by the next such parcel.
        """
        for domain, name, slug in HAND_OFF_CARRIERS.values():
            if self._hass.services.has_service(domain, SERVICE_TRACK_PARCEL):
                ir.async_delete_issue(self._hass, DOMAIN, issue_id(domain))
            elif domain in needed:
                url = f"{DOCS_CARRIERS_URL}#{slug}"
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    issue_id(domain),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="carrier_not_set_up",
                    translation_placeholders={"carrier": name, "url": url},
                    learn_more_url=url,
                )

    async def async_process(self, parcels: list[dict]) -> list[dict]:
        """Hand parcels over and return the ones still shown here.

        Nothing is ever untracked at the carrier: a parcel is only handed off
        once it has a real tracking id, so from then on the carrier is the
        authority on it and its own retention cleans it up.
        """
        await self._load()

        if not self.enabled:
            if self._handed:
                self._handed = {}
                await self._save()
            return parcels

        changed = False
        needed: set[str] = set()
        shown: list[dict] = []
        for parcel in parcels:
            code = parcel.get("barcode")
            if code in self._handed:
                continue
            target = _hand_off_target(parcel)
            if (
                target is None
                or parcel["delivered"]
                or code in self._failed
                or not self._started
            ):
                shown.append(parcel)
                continue
            if not self._hass.services.has_service(target, SERVICE_TRACK_PARCEL):
                needed.add(target)
                shown.append(parcel)
            elif await self._track(target, code):
                self._handed[code] = {
                    "domain": target,
                    "since": dt_util.utcnow().isoformat(),
                }
                changed = True
            else:
                self._failed.add(code)
                shown.append(parcel)

        seen = {p.get("barcode") for p in parcels}
        cutoff = dt_util.utcnow() - FORGET_AFTER
        stale = [
            code
            for code, handed in self._handed.items()
            if code not in seen
            and (since := dt_util.parse_datetime(handed["since"])) is not None
            and since < cutoff
        ]
        for code in stale:
            del self._handed[code]
            changed = True

        if self._started:
            self._sync_issues(needed)
        if changed:
            await self._save()
        return shown

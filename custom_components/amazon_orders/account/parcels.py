"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. The status resolution and
:func:`normalize_parcel` are the carrier-specific parts; the timestamp
parsing, history builder, sort contract, delivered filter and the one-shot
warning for unmapped statuses are suite-wide machinery.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)
from .locales import (
    DELIVERED_EVENTS,
    EVENTS,
    UNNAMED_CARRIER_HEADERS,
    classify_order_text,
)

_LOGGER = logging.getLogger(__name__)

# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-amazon/issues/new"
    "?template=unrecognised_status.yml"
)

# Progress-tracker milestones, only the ones a real page has shown; any other
# name is resolved through the timeline instead.
_MILESTONE_MAP: dict[str, ParcelStatus] = {
    "ORDERED": ParcelStatus.REGISTERED,
    "SHIPPED": ParcelStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
}

# The tracking page's language-independent status code. Anything not listed
# is reported once so the map can grow. ``PICKED_UP`` is the recipient
# collecting the parcel, e.g. from a parcel locker.
_SHORT_STATUS_MAP: dict[str, ParcelStatus] = {
    "ORDER_PLACED": ParcelStatus.REGISTERED,
    "IN_TRANSIT": ParcelStatus.IN_TRANSIT,
    "OUT_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
    "PICKED_UP": ParcelStatus.DELIVERED,
}

# A shipment Amazon cannot follow, such as a letter sent without tracking
# (#47): it stays in transit for good, so once its promised day is this far
# behind it counts as delivered on that day.
UNTRACKABLE_EXPECTED_DELIVERED = "UNTRACKABLE_STILL_EXPECTED_DELIVERED"
UNTRACKABLE_GRACE = timedelta(days=3)

_CARRIER_NAMES = {
    "DRAGONFLY": "Dragonfly",
    "DHL_CONNECT": "DHL",
    # DPD Germany (#41).
    "DPDDE": "DPD",
    "Amazon": "Amazon",
    "COLIS_PRIVE_BELU": "Colis Privé",
    # Not an Amazon code: guessed from the tracking id (pages._guess_carrier).
    "LA_POSTE": "La Poste",
}

# Status texts we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Amazon status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def warn_once(key: str, message: str) -> None:
    """Log a one-shot warning with a copy-paste issue link.

    Only vocabulary and structure may go in ``message`` — never a name, an
    address or an id.
    """
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning("%s Open an issue and paste this line: %s", message, NEW_ISSUE_URL)


def _event_status(message: str | None) -> ParcelStatus | None:
    """Look a timeline message up without logging anything."""
    if not message:
        return None
    # Amazon ends some timeline messages with a full stop and some without.
    text = message.strip().lower().rstrip(".!").replace("’", "'")
    mapped = EVENTS.get(text)
    if mapped is not None:
        return mapped
    if text.startswith(DELIVERED_EVENTS):
        return ParcelStatus.DELIVERED
    return None


def map_event_status(message: str | None) -> ParcelStatus | None:
    """Map a timeline message to a canonical status, or ``None``.

    Unmapped messages keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once.
    """
    if not message:
        return None
    mapped = _event_status(message)
    if mapped is None:
        _warn_unmapped_status(f"event={message.strip()}")
    return mapped


def _order_text_status(text: str | None) -> ParcelStatus | None:
    """Map order-line wording, reporting once when only a likely guess matches."""
    kind, confirmed = classify_order_text(text)
    if not isinstance(kind, ParcelStatus):
        return None
    if not confirmed:
        masked = re.sub(r"\d+", "#", text or "")
        warn_once(
            f"plausible-status={masked}",
            f'The Amazon order-line text "{masked}" was read as {kind.value}'
            " from wording we have not seen confirmed yet; please confirm it"
            " is right.",
        )
    return kind


def resolve_status(raw: dict) -> ParcelStatus:
    """Resolve a status: milestone, short status, newest event, order text.

    Only when none of them maps does the parcel report ``unknown``, with a
    one-shot warning naming what was seen. An unmapped milestone or short
    status is reported once on its own as well, even when a later source
    resolves the status.
    """
    if assumed_delivered_on(raw) is not None:
        return ParcelStatus.DELIVERED
    milestone = raw.get("milestone")
    mapped = _MILESTONE_MAP.get(milestone) if milestone else None
    if mapped is not None:
        return mapped
    short_status = (raw.get("page_state") or {}).get("shortStatus")
    if isinstance(short_status, str) and short_status:
        mapped = _SHORT_STATUS_MAP.get(short_status)
        if mapped is not None:
            return mapped
        # Language-independent, so worth growing even when other sources resolve.
        _warn_unmapped_status(f"short_status={short_status}")
    status = _resolve_without_milestone(raw, milestone)
    if milestone and status is not ParcelStatus.UNKNOWN:
        # The milestone is the preferred source but its vocabulary is barely
        # known, so report a new one even when the timeline can stand in.
        _warn_unmapped_status(f"milestone={milestone}")
    return status


def assumed_delivered_on(raw: dict) -> date | None:
    """Return the promised day of an untrackable shipment past its grace."""
    state = raw.get("page_state") or {}
    if state.get("exceptionStateIdentifier") != UNTRACKABLE_EXPECTED_DELIVERED:
        return None
    try:
        promised = date.fromisoformat(str(raw.get("promised_on")))
    except ValueError:
        return None
    if dt_util.now().date() <= promised + UNTRACKABLE_GRACE:
        return None
    return promised


def _resolve_without_milestone(raw: dict, milestone: str | None) -> ParcelStatus:
    """Fall back to the newest mappable event, then the order text."""
    events = sorted(
        (e for e in raw.get("events") or [] if isinstance(e, dict)),
        key=lambda e: (
            parse_iso(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    for event in events:
        mapped = _event_status(event.get("message"))
        if mapped is not None:
            return mapped

    mapped = _order_text_status(raw.get("order_status"))
    if mapped is not None:
        return mapped

    # Nothing recognised, a line with no tracking page included: it stays
    # unknown rather than being guessed to be a parcel that is on its way.
    newest = events[0].get("message") if events else None
    order_text = re.sub(r"\d+", "#", raw.get("order_status") or "")
    _warn_unmapped_status(
        f"milestone={milestone} event={newest} order_status={order_text}"
    )
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the shipment's timeline.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Sorted oldest → newest and capped to the
    most recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = event.get("timestamp")
        if not timestamp:
            continue
        message = event.get("message")
        entry = {
            "timestamp": str(timestamp),
            "status": map_event_status(message),
            "raw_status": message,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _barcode(raw: dict) -> str | None:
    """Return the tracking id, else a stable key for the shipment itself."""
    if raw.get("tracking_id"):
        return str(raw["tracking_id"])
    shipment = raw.get("shipment_id")
    if not shipment:
        return None
    package = raw.get("package_id")
    return str(shipment) if package in (None, "", "1") else f"{shipment}-{package}"


def _carrier(raw: dict) -> str:
    code = raw.get("carrier_code")
    if not code:
        header = raw.get("carrier_header")
        if header and header.lower() not in UNNAMED_CARRIER_HEADERS:
            masked = re.sub(r"\d+", "#", header)
            warn_once(
                f"carrier-header={masked}",
                f'Amazon named the delivery carrier as "{masked}", which we'
                " could not read; please share it so it can be recognised.",
            )
        elif raw.get("tracking_id") and raw.get("milestone") != "ORDERED":
            warn_once(
                "carrier=missing",
                "Amazon's tracking page named no delivery carrier we could"
                " find; please share which carrier the page shows.",
            )
        return "Amazon"
    name = _CARRIER_NAMES.get(code)
    if name is None:
        warn_once(
            f"carrier={code}",
            f"Amazon named a delivery carrier we have not seen before (carrier_code={code}).",
        )
        name = str(code).replace("_", " ").title()
    return name


def tracking_url(raw: dict) -> str | None:
    """Return the shipment's page on the storefront it was read from."""
    path = raw.get("track_path") or raw.get("pop_path")
    if not path:
        return None
    if str(path).startswith("http"):
        return str(path)
    return f"https://www.{raw.get('domain')}{path}"


def _delivered_at(raw: dict) -> str | None:
    """When the shipment was delivered: the delivered event, else the day."""
    stamps = [
        (parsed, event["timestamp"])
        for event in raw.get("events") or []
        if isinstance(event, dict)
        and _event_status(event.get("message")) is ParcelStatus.DELIVERED
        and (parsed := parse_iso(event.get("timestamp"))) is not None
    ]
    if stamps:
        return max(stamps)[1]
    try:
        day = date.fromisoformat(str(raw.get("delivered_on")))
    except ValueError:
        day = assumed_delivered_on(raw)
        if day is None:
            return None
    # A bare day is the user's day: midnight in Home Assistant's zone, not UTC.
    return dt_util.start_of_local_day(day).isoformat()


def _raw_status(raw: dict) -> str | None:
    """Return the most specific text Amazon gave: newest event, else order line."""
    newest = sorted(
        (e for e in raw.get("events") or [] if isinstance(e, dict)),
        key=lambda e: (
            parse_iso(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
    if newest and newest[-1].get("message"):
        return newest[-1]["message"]
    return raw.get("order_status") or raw.get("milestone")


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the shipment record as ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key is ``None`` when Amazon
    does not expose it — never omitted. Amazon's pages carry no sender,
    receiver, weight or dimensions, and no pickup point name; the delivery window only
    when the tracking page predicts one.
    """
    status = resolve_status(raw)
    delivered = status is ParcelStatus.DELIVERED
    window = raw.get("delivery_window")
    planned_from, planned_to = planned_for_status(
        status,
        *(
            planned_window(*window, zone=dt_util.get_default_time_zone())
            if window
            else (None, None)
        ),
    )

    return {
        "carrier": _carrier(raw),
        "barcode": _barcode(raw),
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _raw_status(raw),
        "delivered": delivered,
        "delivered_at": _delivered_at(raw) if delivered else None,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": is_pickup(
            None,
            status,
            (
                map_event_status(event.get("message"))
                for event in raw.get("events") or []
                if isinstance(event, dict)
            ),
        ),
        "pickup_point": None,
        "url": tracking_url(raw),
        "weight": None,
        "dimensions": None,
        "history": build_history(raw.get("events")) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]

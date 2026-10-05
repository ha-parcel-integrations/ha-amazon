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
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry

from ..const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-amazon/issues/new"
    "?template=unrecognised_status.yml"
)

# Progress-tracker milestones. Only ``DELIVERED`` has been observed; the
# other milestone names are resolved through the timeline instead.
_MILESTONE_MAP: dict[str, ParcelStatus] = {
    "DELIVERED": ParcelStatus.DELIVERED,
}

# Timeline messages, lower-cased.
_EVENT_MAP: dict[str, ParcelStatus] = {
    "package arrived at an amazon facility": ParcelStatus.IN_TRANSIT,
    "package departed an amazon facility": ParcelStatus.IN_TRANSIT,
    "parcel arrived at a carrier facility": ParcelStatus.IN_TRANSIT,
    "package left the courier facility": ParcelStatus.IN_TRANSIT,
    "out for delivery": ParcelStatus.OUT_FOR_DELIVERY,
    "delivered to customer": ParcelStatus.DELIVERED,
    "delivered to letterbox": ParcelStatus.DELIVERED,
}

# Order-line status text, for shipments without a tracking page.
_ORDER_TEXT_MAP: tuple[tuple[str, ParcelStatus], ...] = (
    ("delivered", ParcelStatus.DELIVERED),
    ("not yet dispatched", ParcelStatus.REGISTERED),
)

_CARRIER_NAMES = {
    "DRAGONFLY": "Dragonfly",
    "DHL_CONNECT": "DHL",
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
    text = message.strip().lower()
    mapped = _EVENT_MAP.get(text)
    if mapped is not None:
        return mapped
    if text.startswith("delivered"):
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
    if not text:
        return None
    lowered = text.strip().lower()
    for prefix, status in _ORDER_TEXT_MAP:
        if lowered.startswith(prefix):
            return status
    return None


def resolve_status(raw: dict) -> ParcelStatus:
    """Resolve a record's status: milestone, else newest event, else order text.

    Only when none of them maps does the parcel report ``unknown``, with a
    one-shot warning naming what was seen. An unmapped milestone is reported
    once on its own as well, even when the timeline resolves the status.
    """
    milestone = raw.get("milestone")
    mapped = _MILESTONE_MAP.get(milestone) if milestone else None
    if mapped is not None:
        return mapped
    status = _resolve_without_milestone(raw, milestone)
    if milestone and status is not ParcelStatus.UNKNOWN:
        # The milestone is the preferred source but its vocabulary is barely
        # known, so report a new one even when the timeline can stand in.
        _warn_unmapped_status(f"milestone={milestone}")
    return status


def _resolve_without_milestone(raw: dict, milestone: str | None) -> ParcelStatus:
    """Fall back to the newest mappable event, then the order text."""
    events = sorted(
        (e for e in raw.get("events") or [] if isinstance(e, dict)),
        key=lambda e: parse_iso(e.get("timestamp")) or datetime.min.replace(
            tzinfo=timezone.utc
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

    if not raw.get("track_path") and not raw.get("milestone") and not events:
        # No tracking page and not delivered: Amazon has not dispatched it.
        # The line's own wording is reported once so the map can grow.
        text = re.sub(r"\d+", "#", raw.get("order_status") or "")
        if text:
            _warn_unmapped_status(f"order_status={text} (treated as registered)")
        return ParcelStatus.REGISTERED

    if milestone or events or raw.get("order_status"):
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
    day = parse_iso(raw.get("delivered_on"))
    return day.isoformat() if day else None


def _raw_status(raw: dict) -> str | None:
    """Return the most specific text Amazon gave: newest event, else order line."""
    newest = sorted(
        (e for e in raw.get("events") or [] if isinstance(e, dict)),
        key=lambda e: parse_iso(e.get("timestamp"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    if newest and newest[-1].get("message"):
        return newest[-1]["message"]
    return raw.get("order_status") or raw.get("milestone")


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the shipment record as ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key is ``None`` when Amazon
    does not expose it — never omitted. Amazon's pages carry no sender,
    receiver, weight, dimensions, pickup point or reliable delivery window.
    """
    status = resolve_status(raw)
    delivered = status is ParcelStatus.DELIVERED

    return {
        "carrier": _carrier(raw),
        "barcode": _barcode(raw),
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _raw_status(raw),
        "delivered": delivered,
        "delivered_at": _delivered_at(raw) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
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
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
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

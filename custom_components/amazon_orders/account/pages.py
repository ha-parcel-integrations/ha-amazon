"""Parse the signed-in Amazon order, order-line and shipment-tracking pages.

Pure functions over HTML text. Wherever the page embeds JSON state that is
preferred over the visible text; the visible text is only read for what the
state does not carry (the order line's status, the event timeline).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TILE_RE = re.compile(
    r'<a class="item-card__link" href="([^"]*)" aria-label="([^"]*)"', re.S
)
_PLACED_RE = re.compile(
    r"Order placed (\d{1,2} [A-Za-z]+ \d{4})(?:, (?P<tail>.*))?$", re.S
)
_ORDER_ID_RE = re.compile(r"\d{3}-\d{7}-\d{7}")
# Status texts that can open an aria-label segment when the "Order placed"
# anchor is missing (storefronts lay the label out differently).
_STATUS_SEGMENT_PREFIXES = (
    "delivered",
    "arriving",
    "not yet dispatched",
    "dispatched",
    "out for delivery",
)
_DAY_MONTH_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?")
_TRACK_LINK_RE = re.compile(r'href="([^"]*/ship-track\?[^"]*)"')
_CARRIER_RE = re.compile(r"Delivery By ([A-Za-z0-9_]+)")
_TRACKING_ID_RE = re.compile(r"Tracking ID:\s*([A-Za-z0-9-]+)")
_STATE_RE = re.compile(
    r'<script[^>]*page-state[^>]*>\s*(\{.*?\})\s*</script>', re.S
)
_EVENT_PART_RE = re.compile(
    r'<span class="tracking-event-(date|time|message|location)">([^<]*)</span>'
)
_PASSWORD_FIELD_RE = re.compile(r'<input[^>]+type="password"', re.I)

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january february march april may june july "
            "august september october november december"
        ).split(),
        start=1,
    )
}

# Third-party map key the page embeds; it is not parcel data.
_STATE_DROP = {"hereMapsApiKey"}


@dataclass(frozen=True)
class OrderTile:
    """One order line on the orders page."""

    order_id: str
    line_item_id: str
    shipment_id: str
    package_id: str
    pop_path: str
    title: str
    status_text: str
    status_note: str | None
    placed_on: date | None
    delivered_on: date | None
    position: int = 0

    @property
    def shipment_key(self) -> str:
        """The shipment id, or a stable stand-in for a line not yet shipped.

        An order that has not been dispatched has no shipment id; its line id
        (else its place on the page) keeps it distinct, and the key is
        replaced by the real shipment id once one exists.
        """
        if self.shipment_id:
            return self.shipment_id
        return f"{self.order_id}-{self.line_item_id or self.position}"

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of the shipment this line belongs to."""
        return (self.order_id, self.shipment_key, self.package_id)

    @property
    def delivered(self) -> bool:
        """Whether the line already reads as delivered."""
        return self.status_text.lower().startswith("delivered")


@dataclass
class TrackInfo:
    """What a ship-track page carries."""

    tracking_id: str | None
    carrier_code: str | None
    timezone: str | None
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


def looks_signed_out(page: str) -> bool:
    """Whether the page is a sign-in form instead of the requested content."""
    return bool(_PASSWORD_FIELD_RE.search(page))


def _day_month(text: str, reference: date, *, forward: bool = False) -> date | None:
    """Resolve "3 October" (year optional) against a reference date.

    By default the date is taken to be in the past (the year is rolled back if
    it would land in the future); with ``forward`` it is taken to fall on or
    after the reference (the year is rolled on if it would land before it).
    """
    match = _DAY_MONTH_RE.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    year = int(match.group(3)) if match.group(3) else reference.year
    try:
        found = date(year, month, int(match.group(1)))
    except ValueError:
        return None
    if match.group(3):
        return found
    if forward and found < reference:
        return found.replace(year=found.year + 1)
    if not forward and found > reference + timedelta(days=2):
        return found.replace(year=found.year - 1)
    return found


def parse_order_tiles(page: str, today: date) -> list[OrderTile]:
    """Return every order line found on an orders page, in page order."""
    matches = list(_TILE_RE.finditer(page))
    tiles: list[OrderTile] = []
    for position, match in enumerate(matches):
        href = html.unescape(match.group(1))
        label = html.unescape(match.group(2))
        query = {k.lower(): v for k, v in parse_qs(urlparse(href).query).items()}
        order_id = (query.get("orderid") or [""])[0]
        if not order_id:
            found = _ORDER_ID_RE.search(href)
            order_id = found.group(0) if found else ""
        if not order_id:
            continue
        # The aria-label is the one place every tile layout agrees on:
        # "<title>, Order placed <date>, <status>[, <note>]".
        placed = _PLACED_RE.search(label)
        placed_on = _day_month(placed.group(1), today) if placed else None
        title = label.split(", Order placed")[0]
        if placed:
            status_text, _, note = (placed.group("tail") or "").partition(", ")
        else:
            title, status_text, note = _split_unanchored_label(label)
        delivered_on = None
        if status_text.lower().startswith("delivered"):
            delivered_on = (
                _day_month(status_text, placed_on, forward=True)
                if placed_on
                else _day_month(status_text, today)
            )
        tiles.append(
            OrderTile(
                order_id=order_id,
                line_item_id=(query.get("lineitemid") or [""])[0],
                shipment_id=(query.get("shipmentid") or [""])[0],
                package_id=(query.get("packageid") or ["1"])[0],
                pop_path=href,
                title=title,
                status_text=status_text.strip(),
                status_note=note or None,
                placed_on=placed_on,
                delivered_on=delivered_on,
                position=position,
            )
        )
    return tiles


def _split_unanchored_label(label: str) -> tuple[str, str, str]:
    """Split "<title>, <status>[, <note>]" when there is no "Order placed".

    The title may itself contain commas, so the status is the first segment
    after it that opens with a known status text.
    """
    segments = label.split(", ")
    for index in range(1, len(segments)):
        if segments[index].lower().startswith(_STATUS_SEGMENT_PREFIXES):
            return (
                ", ".join(segments[:index]),
                segments[index],
                ", ".join(segments[index + 1 :]),
            )
    return label, "", ""


def parse_track_link(page: str) -> str | None:
    """Return the ship-track path from an order-line page, if it has one."""
    match = _TRACK_LINK_RE.search(page)
    return html.unescape(match.group(1)) if match else None


def _page_state(page: str) -> dict[str, Any]:
    match = _STATE_RE.search(page)
    if not match:
        return {}
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return {}
    return {key: value for key, value in state.items() if key not in _STATE_DROP}


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _parse_time(text: str) -> time | None:
    try:
        return datetime.strptime(text.strip().upper(), "%I:%M %p").time()
    except ValueError:
        return None


def _parse_events(
    page: str, zone: ZoneInfo | None, today: date
) -> list[dict[str, Any]]:
    """Read the timeline: date headers, then time / message / location."""
    events: list[dict[str, Any]] = []
    current_day: date | None = None
    pending: dict[str, Any] | None = None
    for kind, raw_text in _EVENT_PART_RE.findall(page):
        text = html.unescape(raw_text).strip()
        if kind == "date":
            current_day = _day_month(text, today)
        elif kind == "time":
            pending = {"day": current_day, "time": _parse_time(text)}
        elif pending is not None and kind == "message":
            pending["message"] = text
        elif pending is not None and kind == "location":
            pending["location"] = text or None
            events.append(pending)
            pending = None
    parsed: list[dict[str, Any]] = []
    for event in events:
        if event["day"] is None or not event.get("message"):
            continue
        moment = datetime.combine(
            event["day"], event["time"] or time(0, 0), tzinfo=zone
        )
        parsed.append(
            {
                "timestamp": moment.isoformat(),
                "message": event["message"],
                "location": event["location"],
            }
        )
    return parsed


def parse_track_page(page: str, today: date) -> TrackInfo:
    """Parse a ship-track page into carrier, tracking id, state and timeline."""
    state = _page_state(page)
    carrier = _CARRIER_RE.search(page)
    tracking_match = _TRACKING_ID_RE.search(page)
    tracking_id = state.get("trackingId") or (
        tracking_match.group(1) if tracking_match else None
    )
    timezone = state.get("timezone")
    if not isinstance(timezone, str):
        timezone = None
    return TrackInfo(
        tracking_id=str(tracking_id) if tracking_id else None,
        carrier_code=carrier.group(1) if carrier else None,
        timezone=timezone,
        events=_parse_events(page, _zone(timezone), today),
        state=state,
    )


def build_record(
    domain: str,
    tiles: list[OrderTile],
    track_path: str | None,
    track: TrackInfo | None,
) -> dict[str, Any]:
    """Assemble the raw shipment record the parcel mapping works on.

    ``tiles`` are the order lines of one shipment; the first one speaks for
    the shipment's status text.
    """
    lead = tiles[0]
    tracking_id = track.tracking_id if track else None
    progress = (track.state.get("progressTracker") if track else None) or {}
    return {
        "domain": domain,
        "order_id": lead.order_id,
        "shipment_id": lead.shipment_key,
        "package_id": lead.package_id,
        "barcode_source": "tracking_id" if tracking_id else "shipment_key",
        "tracking_id": tracking_id,
        "carrier_code": track.carrier_code if track else None,
        "order_status": lead.status_text or None,
        "order_status_note": lead.status_note,
        "delivered_on": lead.delivered_on.isoformat() if lead.delivered_on else None,
        "milestone": progress.get("lastReachedMilestone") if progress else None,
        "events": track.events if track else [],
        "items": [tile.title for tile in tiles],
        "pop_path": lead.pop_path,
        "track_path": track_path,
        "page_state": track.state if track else {},
    }

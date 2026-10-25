"""Parse the signed-in Amazon order, order-line and shipment-tracking pages.

Pure functions over HTML text. Wherever the page embeds JSON state that is
preferred over the visible text; the visible text is only read for what the
state does not carry (the order line's status, the event timeline).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..const import ParcelStatus
from .parcels import warn_once
from .vocabulary import (
    MONTHS,
    SKIP,
    STATUS_PREFIXES,
    UNNAMED_CARRIER_HEADERS,
    classify_order_text,
)

_TILE_RE = re.compile(
    r'<a class="item-card__link" href="([^"]*)" aria-label="([^"]*)"', re.S
)
_PLACED_ANCHOR = r"(?:Order placed|Commande effectuée le)"
_PLACED_RE = re.compile(
    _PLACED_ANCHOR + r" (\d{1,2} [^\W\d_]+ \d{4})(?:, (?P<tail>.*))?$", re.S
)
_TITLE_END_RE = re.compile(", " + _PLACED_ANCHOR)
_ORDER_ID_RE = re.compile(r"\d{3}-\d{7}-\d{7}")
# "3 October", "3. Oktober", "3 de octubre", "3 Oct. 2026".
_DAY_MONTH_RE = re.compile(
    r"(\d{1,2})\.?\s+(?:de\s+)?([^\W\d_]+)\.?(?:\s+(?:de\s+)?(\d{4}))?"
)
_TRACK_LINK_RE = re.compile(r'href="([^"]*/ship-track\?[^"]*)"')
# A sub-path such as the delivery-feedback survey is not a tracking page.
_TRACK_PAGE_PATH_RE = re.compile(
    r"/(?:progress-tracker/package|gp/your-account/ship-track)(?:/ref=[^/]*)?/?$"
)
_CARRIER_RE = re.compile(
    r"(?:Delivery By|Livraison par|Bezorging door|Levering door|Versendet mit|Versand durch|Zustellung durch)"
    r" ([A-Za-z0-9_]+)"
)
# amazon.de names the courier plainly instead of by its internal code.
_CARRIER_ALIASES = {"DHL": "DHL_CONNECT"}
_CARRIER_HEADER_RE = re.compile(
    r'(?:pt-delivery-card-wrapper"><div><h3|tracking-event-carrier-header">\s*<h2)'
    r"[^>]*>\s*([^<]*?)\s*</h[23]>"
)
_TRACKING_ID_RE = re.compile(
    r"(?:Tracking[\s-]?ID|Numéro de suivi)\s*:\s*([A-Za-z0-9-]+)", re.I
)
_STATE_RE = re.compile(r"<script[^>]*page-state[^>]*>\s*(\{.*?\})\s*</script>", re.S)
_EVENT_PART_RE = re.compile(
    r'<span class="tracking-event-(date|time|message|location)">([^<]*)</span>'
)
_PASSWORD_FIELD_RE = re.compile(r'<input[^>]+type="password"', re.I)
# Proof-of-delivery photo on the delivery card. The visible images are
# lazy-loaded, so their real URL is in ``data-src`` and ``src`` only holds a
# placeholder; the ``<noscript>`` twin inside the same popup carries the URL in
# a plain ``src`` and survives a class rename, so it is tried as well.
_POD_REGION_RE = re.compile(
    r"(?:pt-delivery-card-pod-popup|a-popover-pt-delivery-card-pod-popup-content)"
    r".*?(?:</div>|</span>)",
    re.S,
)
_POD_IMG_RE = re.compile(r"<img\b[^>]*>", re.S)
# The photo is a presigned S3 link: it stops working a fixed number of seconds
# after it was signed, which is when we read the page, not when the parcel was
# delivered. Publishing the moment it dies lets a dashboard hide a dead image.
_POD_SIGNED_AT = "X-Amz-Date"
_POD_VALID_FOR = "X-Amz-Expires"
_POD_SRC_RE = re.compile(r'\b(data-src|src)="([^"]+)"')

# "14:00 - 16:30" in the promise message, with the countdown to its end.
_WINDOW_RE = re.compile(
    r"(\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)\s*[-–]\s*(\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)"
)
_WINDOW_END_KEY = "timeUntilPredictedDeliveryWindowEnd"
_WINDOW_TOLERANCE = timedelta(hours=1)
# A courier the page does not name: the Colissimo id shape, amazon.fr only.
_LA_POSTE_ID_RE = re.compile(r"\d[A-Z]\d{11}")
_LA_POSTE_STOREFRONTS = {"amazon.fr"}
LA_POSTE_GUESS = "LA_POSTE"

# Amazon Business: the orders page is an empty shell and the list comes from a
# separate call, whose parameters the shell carries.
_BUSINESS_TOKEN_RE = re.compile(
    r'<meta name="anti-csrftoken-a2z" content="([^"]+)"[^>]*id="ab-your-orders-anticsrf-token"'
)
_BUSINESS_STATE_RE = re.compile(
    r"<script[^>]*yourOrdersStateData[^>]*>\s*(\{.*?\})\s*</script>", re.S
)
_BUSINESS_REQUEST_ID_RE = re.compile(
    r'<input[^>]*name="originalRequestId" value="([^"]*)"'
)
_BUSINESS_CARD_RE = re.compile(
    r'<a href="([^"]*)" class="[^"]*\b(nonCancelledDeliveryCard|cancelledDeliveryCard)\b[^"]*">(.*?)</a>',
    re.S,
)
_BUSINESS_STATUS_RE = re.compile(
    r'<p class="primaryMessage">\s*<span[^>]*>([^<]*)</span>'
)
_BUSINESS_NOTE_RE = re.compile(
    r'<p class="secondaryMessage">\s*<span[^>]*>([^<]*)</span>'
)
_BUSINESS_TITLE_RE = re.compile(r'<span class="a-truncate-full">([^<]*)</span>')
# Sent camelCase; a browser's DOM shows them lower-cased.
_BUSINESS_COUNT_RE = re.compile(r'data-noofdeliverycardsrendered="(\d+)"', re.I)
_BUSINESS_EXCEPTION_RE = re.compile(r'data-isexception="true"', re.I)
BUSINESS_HISTORY_PATH = "/ab/your-orders/deliveryHistory"
# Every order of the account from the last three months, newest first: any
# buyer, any payer, any organisation. The page's own fallback is the narrowest
# option (yours, paid by you). Only the ids are read. Never pair "ordered by
# all" with "paid by you": that silently returns nothing.
_BUSINESS_FILTERS = {
    "orderCondition": {"displayText": "Orders", "id": "yoOrdersTabination"},
    "time": {"displayText": "past 3 months", "id": "yoPast3months"},
    "sortBy": {"displayText": "Order Date", "id": "yoOrderDate"},
    "users": {"displayText": "Ordered by all", "id": "yoOrderedByAll"},
    "paymentType": {"displayText": "All payments", "id": "yoAllPayments"},
    "legalEntities": {"displayText": "All organizations", "id": "yoAllOrganizations"},
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
    # False for a line whose link leads to no tracking (a Business order page).
    follow: bool = True

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
        return classify_order_text(self.status_text)[0] is ParcelStatus.DELIVERED


@dataclass
class TrackInfo:
    """What a ship-track page carries."""

    tracking_id: str | None
    carrier_code: str | None
    timezone: str | None
    carrier_header: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    delivery_photo_url: str | None = None
    delivery_photo_expires: str | None = None


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
    month = MONTHS.get(match.group(2).lower())
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
        title = _TITLE_END_RE.split(label, maxsplit=1)[0]
        if placed:
            status_text, _, note = (placed.group("tail") or "").partition(", ")
        else:
            title, status_text, note = _split_unanchored_label(label)
        kind, confirmed = classify_order_text(status_text)
        if kind is not None and not confirmed:
            warn_once(
                f"plausible-line={_mask(status_text)}",
                f'The Amazon order-line text "{_mask(status_text)}" was read as'
                f" {kind} from wording we have not seen confirmed yet; please"
                " confirm it is right.",
            )
        if kind == SKIP:
            continue
        delivered_on = None
        if kind is ParcelStatus.DELIVERED:
            delivered_on = (
                _day_month(status_text, placed_on, forward=True)
                if placed_on
                else _day_month(status_text, today)
            ) or _relative_day(status_text, today)
            if delivered_on is None:
                _warn_undated(status_text)
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


@dataclass(frozen=True)
class BusinessRequest:
    """What the Business order-list call needs, read off the orders shell."""

    token: str
    body: dict[str, Any]

    def form(self, page_number: int) -> dict[str, str]:
        """Build the form for one page of the list (0 is the newest)."""
        return {
            "deliveryHistoryRequestString": json.dumps(
                {**self.body, "pageNumber": page_number}
            ),
            "refMarker": f"ab_ppx_yo_mob_pagination_{page_number}",
        }


def parse_business_shell(page: str) -> BusinessRequest | None:
    """Return the Business order-list call for an orders shell, else ``None``."""
    token = _BUSINESS_TOKEN_RE.search(page)
    state_match = _BUSINESS_STATE_RE.search(page)
    if not token or not state_match:
        return None
    try:
        state = json.loads(html.unescape(state_match.group(1)))
    except ValueError:
        return None
    ids = {key: state.get(key) for key in ("marketplaceId", "businessId", "sessionId")}
    if not all(isinstance(value, str) and value for value in ids.values()):
        return None
    # ``searchKeyword`` must be null: an empty string silently returns no order.
    body: dict[str, Any] = {
        **ids,
        "pageNumber": 0,
        "searchKeyword": None,
        "isTesting": False,
        "isDefaultFilterSelected": False,
        "filters": _BUSINESS_FILTERS,
    }
    if request_id := _BUSINESS_REQUEST_ID_RE.search(page):
        body["originalRequestId"] = request_id.group(1)
    return BusinessRequest(token=html.unescape(token.group(1)), body=body)


def business_history_failed(fragment: str) -> bool:
    """Whether the Business order list reports a server-side error."""
    return bool(_BUSINESS_EXCEPTION_RE.search(fragment))


def business_cards_found(fragment: str) -> int:
    """How many cards of the Business order list are in a shape we read."""
    return len(_BUSINESS_CARD_RE.findall(fragment))


def business_card_count(fragment: str) -> int | None:
    """How many cards the Business order list says it rendered."""
    match = _BUSINESS_COUNT_RE.search(fragment)
    return int(match.group(1)) if match else None


def _text(raw: str) -> str:
    return " ".join(html.unescape(raw).split())


def parse_business_cards(fragment: str, today: date) -> list[OrderTile]:
    """Return the order lines of a Business order list, in page order.

    A card carries no order date and no package id; an in-flight card links
    straight to its tracking page, a delivered one to its order page.
    """
    tiles: list[OrderTile] = []
    for position, match in enumerate(_BUSINESS_CARD_RE.finditer(fragment)):
        href = html.unescape(match.group(1))
        body = match.group(3)
        status = _BUSINESS_STATUS_RE.search(body)
        status_text = _text(status.group(1)) if status else ""
        kind, confirmed = classify_order_text(status_text)
        if match.group(2) == "cancelledDeliveryCard" or kind == SKIP:
            continue
        query = {k.lower(): v for k, v in parse_qs(urlparse(href).query).items()}
        order_id = (query.get("orderid") or query.get("oid") or [""])[0]
        if not order_id:
            warn_once(
                "business-card-without-order",
                "An Amazon Business order card named no order, so it is left"
                " out; Amazon may have changed how the list links its orders.",
            )
            continue
        if kind is not None and not confirmed:
            warn_once(
                f"plausible-line={_mask(status_text)}",
                f'The Amazon order-line text "{_mask(status_text)}" was read as'
                f" {kind} from wording we have not seen confirmed yet; please"
                " confirm it is right.",
            )
        title_match = _BUSINESS_TITLE_RE.search(body)
        title = _text(title_match.group(1)) if title_match else ""
        note = _BUSINESS_NOTE_RE.search(body)
        # A delivered card names no item; its title keeps it apart from the
        # order's other lines without depending on its place in the list.
        line_item = (query.get("itemid") or [""])[0] or (
            "t" + hashlib.sha1(title.encode()).hexdigest()[:10]
        )
        delivered_on = None
        if kind is ParcelStatus.DELIVERED:
            delivered_on = _day_month(status_text, today) or _relative_day(
                status_text, today
            )
            if delivered_on is None:
                _warn_undated(status_text)
        tiles.append(
            OrderTile(
                order_id=order_id,
                line_item_id=line_item,
                shipment_id=(query.get("shipmentid") or [""])[0],
                package_id="1",
                pop_path=href,
                title=title,
                status_text=status_text,
                status_note=_text(note.group(1)) if note else None,
                placed_on=None,
                delivered_on=delivered_on,
                position=position,
                follow=is_track_page_path(href),
            )
        )
    return tiles


def _mask(text: str) -> str:
    """Hide digits so a moving date cannot make a warning repeat."""
    return re.sub(r"\d+", "#", text)


def _warn_undated(status_text: str) -> None:
    """Report a delivered line whose text says more than we could date."""
    # A bare "Delivered" carries no date to read; anything longer should.
    if status_text.strip().lower() in STATUS_PREFIXES:
        return
    masked = _mask(status_text)
    warn_once(
        f"undated-delivery={masked}",
        f'The Amazon order-line text "{masked}" says the parcel was delivered'
        " but gave no date we could read, so the day it was first seen"
        " delivered is used; please share the wording.",
    )


def _split_unanchored_label(label: str) -> tuple[str, str, str]:
    """Split "<title>, <status>[, <note>]" when there is no "Order placed".

    The title may itself contain commas, so the status is the first segment
    after it that opens with a known status text.
    """
    segments = label.split(", ")
    for index in range(1, len(segments)):
        if segments[index].lower().startswith(STATUS_PREFIXES):
            return (
                ", ".join(segments[:index]),
                segments[index],
                ", ".join(segments[index + 1 :]),
            )
    return label, "", ""


def is_track_page_path(path: str) -> bool:
    """Whether an order-line link already points at the tracking page itself."""
    return bool(_TRACK_PAGE_PATH_RE.search(urlparse(path).path))


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


def page_timezone(page: str) -> str | None:
    """Return the IANA zone name a ship-track page declares, if any."""
    name = _page_state(page).get("timezone")
    return name if isinstance(name, str) else None


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        # suite: allow zoneinfo (loaded off the event loop by the client before parsing)
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


_TODAY_WORDS = {
    "today",
    "vandaag",
    "heute",
    "aujourd'hui",
    "hoy",
    "oggi",
    "idag",
    "dzisiaj",
}
_YESTERDAY_WORDS = {
    "yesterday",
    "gisteren",
    "gestern",
    "hier",
    "ayer",
    "ieri",
    "igår",
    "wczoraj",
}


# Seen on amazon.fr delivered lines: "Livré jeudi", within the past week.
_WEEKDAYS = {
    name: number
    for number, name in enumerate(
        ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    )
}
_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def _relative_day(text: str, today: date) -> date | None:
    """Resolve "today", "yesterday" or a weekday name in an order-line text."""
    for word in _WORD_RE.findall(text.lower().replace("’", "'")):
        if word in _TODAY_WORDS:
            return today
        if word in _YESTERDAY_WORDS:
            return today - timedelta(days=1)
        if word in _WEEKDAYS:
            return today - timedelta(days=(today.weekday() - _WEEKDAYS[word]) % 7 or 7)
    return None


# "Arriving tomorrow", "Arriving Oct 27", "Arriving Oct 27-29", "Arriving Oct 27-Nov 2":
# the English forms seen on a real storefront; a timed "Arriving today 11:00 AM - ..."
# is the delivery window's business and does not match.
_ARRIVING_RE = re.compile(
    r"arriving\s+(?:(?P<word>today|tomorrow)|(?P<m1>[a-z]+)\s+(?P<d1>\d{1,2})"
    r"(?:\s*-\s*(?:(?P<m2>[a-z]+)\s+)?(?P<d2>\d{1,2}))?)",
    re.IGNORECASE,
)


def arrival_days(text: str | None, today: date) -> tuple[date, date] | None:
    """Return the first and last day an order line says the shipment arrives."""
    match = _ARRIVING_RE.fullmatch((text or "").strip())
    if not match:
        return None
    if match["word"]:
        day = today + timedelta(days=match["word"].lower() == "tomorrow")
        return day, day
    month = MONTHS.get(match["m1"].lower())
    end_month = MONTHS.get((match["m2"] or match["m1"]).lower())
    if month is None or end_month is None:
        return None
    try:
        first = date(today.year, month, int(match["d1"]))
        if first < today - timedelta(days=1):
            first = first.replace(year=today.year + 1)
        last = date(first.year, end_month, int(match["d2"] or match["d1"]))
        if last < first:
            last = last.replace(year=last.year + 1)
    except ValueError:
        return None
    return first, last


def _arrival_window(
    text: str | None, track: TrackInfo | None, read_at: datetime | None
) -> tuple[str, str] | None:
    """Return the whole-day window an order line names, in the page's time zone."""
    zone = (_zone(track.timezone) if track else None) or (
        read_at.tzinfo if read_at else None
    )
    if read_at is None or zone is None:
        return None
    days = arrival_days(text, read_at.astimezone(zone).date())
    if days is None:
        return None
    return (
        datetime.combine(days[0], time.min, tzinfo=zone).isoformat(),
        datetime.combine(
            days[1], time.max.replace(microsecond=0), tzinfo=zone
        ).isoformat(),
    )


def _parse_time(text: str) -> time | None:
    for shape in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(text.strip().upper(), shape).time()
        except ValueError:
            continue
    return None


def _event_day(text: str, today: date) -> date | None:
    """Resolve a timeline date header, including "Today" and "Yesterday"."""
    word = text.strip().rstrip(",").lower()
    if word in _TODAY_WORDS:
        return today
    if word in _YESTERDAY_WORDS:
        return today - timedelta(days=1)
    day = _day_month(text, today)
    if day is None and text:
        masked = _mask(text)
        warn_once(
            f"event-date={masked}",
            f'The Amazon tracking timeline has a date "{masked}" we could not'
            " read, so its events are left out; please share it.",
        )
    return day


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
            current_day = _event_day(text, today)
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


def parse_delivery_photo(page: str) -> str | None:
    """Return the proof-of-delivery photo URL from a tracking page, if any.

    Only Amazon's own fleet has shown this card; a third-party carrier's page
    has no such element and yields ``None``.
    """
    for region in _POD_REGION_RE.findall(page):
        for tag in _POD_IMG_RE.findall(region):
            found = dict(_POD_SRC_RE.findall(tag))
            for key in ("data-src", "src"):
                url = html.unescape((found.get(key) or "").strip())
                # A lazy placeholder is an inline data: URI, never the photo.
                if url and not url.startswith("data:"):
                    return url
    return None


def delivery_photo_expiry(url: str | None) -> str | None:
    """Return when a delivery-photo link stops working, if the URL says so."""
    if not url:
        return None
    params = parse_qs(urlparse(url).query)
    signed_at = (params.get(_POD_SIGNED_AT) or [""])[0]
    valid_for = (params.get(_POD_VALID_FOR) or [""])[0]
    if not signed_at or not valid_for.isdigit():
        return None
    try:
        start = datetime.strptime(signed_at, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    expiry = start.replace(tzinfo=timezone.utc) + timedelta(seconds=int(valid_for))
    return expiry.isoformat()


def parse_track_page(page: str, today: date) -> TrackInfo:
    """Parse a ship-track page into carrier, tracking id, state and timeline."""
    state = _page_state(page)
    header = _CARRIER_HEADER_RE.search(page)
    carrier_header = header.group(1) if header and header.group(1) else None
    carrier = _CARRIER_RE.search(carrier_header or page)
    tracking_match = _TRACKING_ID_RE.search(page)
    tracking_id = state.get("trackingId") or (
        tracking_match.group(1) if tracking_match else None
    )
    page_zone = state.get("timezone")
    if not isinstance(page_zone, str):
        page_zone = None
    photo = parse_delivery_photo(page)
    return TrackInfo(
        tracking_id=str(tracking_id) if tracking_id else None,
        carrier_code=_CARRIER_ALIASES.get(carrier.group(1), carrier.group(1))
        if carrier
        else None,
        timezone=page_zone,
        carrier_header=carrier_header,
        events=_parse_events(page, _zone(page_zone), today),
        state=state,
        delivery_photo_url=photo,
        delivery_photo_expires=delivery_photo_expiry(photo),
    )


def delivery_window(state: dict[str, Any], read_at: datetime) -> tuple[str, str] | None:
    """Return the predicted delivery window from a tracking page's state.

    The message names only the times, in the account's language; the countdown
    to the window's end says which day they fall on and confirms the reading.
    """
    countdown = state.get(_WINDOW_END_KEY)
    promise = state.get("promise")
    message = promise.get("promiseMessage") if isinstance(promise, dict) else None
    zone = _zone(
        state.get("timezone") if isinstance(state.get("timezone"), str) else None
    )
    if not isinstance(countdown, int) or isinstance(countdown, bool) or zone is None:
        return None
    match = _WINDOW_RE.search(message) if isinstance(message, str) else None
    start = _parse_time(match.group(1)) if match else None
    end = _parse_time(match.group(2)) if match else None
    expected_end = (read_at + timedelta(milliseconds=countdown)).astimezone(zone)
    if start is not None and end is not None:
        day = expected_end.date()
        window_from = datetime.combine(day, start, tzinfo=zone)
        window_to = datetime.combine(day, end, tzinfo=zone)
        if (
            window_from < window_to
            and abs(window_to - expected_end) <= _WINDOW_TOLERANCE
        ):
            return window_from.isoformat(), window_to.isoformat()
    warn_once(
        f"delivery-window={_mask(message or '')}",
        f'Amazon announced the delivery window "{_mask(message or "")}" in a'
        " shape we could not read, so it is left out; please share it.",
    )
    return None


def _guess_carrier(domain: str, track: TrackInfo) -> str | None:
    """Name the courier from the tracking id when the page does not."""
    if domain not in _LA_POSTE_STOREFRONTS or not track.tracking_id:
        return None
    header = (track.carrier_header or "").lower()
    if header and header not in UNNAMED_CARRIER_HEADERS:
        return None
    if not _LA_POSTE_ID_RE.fullmatch(track.tracking_id):
        return None
    warn_once(
        "carrier-guess=la-poste",
        "An amazon.fr tracking page named no courier, so La Poste (Colissimo)"
        " was guessed from the shape of the tracking id; please confirm the"
        " courier your parcel came with.",
    )
    return LA_POSTE_GUESS


def build_record(
    domain: str,
    tiles: list[OrderTile],
    track_path: str | None,
    track: TrackInfo | None,
    *,
    read_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the raw shipment record the parcel mapping works on.

    ``tiles`` are the order lines of one shipment; the first one speaks for
    the shipment's status text.
    """
    lead = tiles[0]
    tracking_id = track.tracking_id if track else None
    progress = (track.state.get("progressTracker") if track else None) or {}
    carrier_code = track.carrier_code if track else None
    guessed = None
    if track and carrier_code is None:
        guessed = _guess_carrier(domain, track)
    window = (
        delivery_window(track.state, read_at) if track and read_at else None
    ) or _arrival_window(lead.status_text, track, read_at)
    return {
        "domain": domain,
        "order_id": lead.order_id,
        "shipment_id": lead.shipment_key,
        "package_id": lead.package_id,
        "barcode_source": "tracking_id" if tracking_id else "shipment_key",
        "tracking_id": tracking_id,
        "carrier_code": carrier_code or guessed,
        "carrier_guessed": guessed is not None,
        "delivery_photo_url": track.delivery_photo_url if track else None,
        "delivery_photo_expires": track.delivery_photo_expires if track else None,
        "carrier_header": track.carrier_header if track else None,
        "order_status": lead.status_text or None,
        "order_status_note": lead.status_note,
        "delivered_on": lead.delivered_on.isoformat() if lead.delivered_on else None,
        "milestone": progress.get("lastReachedMilestone") if progress else None,
        "delivery_window": list(window) if window else None,
        "events": track.events if track else [],
        "items": [tile.title for tile in tiles],
        "pop_path": lead.pop_path,
        "track_path": track_path,
        "page_state": track.state if track else {},
    }

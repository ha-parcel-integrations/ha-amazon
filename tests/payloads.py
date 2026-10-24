"""Synthetic Amazon pages and shipment records shared by the test modules.

Everything here is invented: ids, names and places do not belong to anyone.
The page builders reproduce only the structure the parser reads.
"""

from __future__ import annotations

import json
from html import escape

COUNTRY = "amazon.nl"
ORDER_ID = "000-0000001-0000001"
ACTIVE_CODE = "AMZNL000000000001"
DELIVERED_CODE = "AMZNL000000000002"
ACTIVE_SHIPMENT = "SHIPactive1"
DELIVERED_SHIPMENT = "SHIPdone001"
FAKE_MAPS_KEY = "fake-maps-key-0000"
FAKE_CUSTOMER = "CUSTOMER00000"
FAKE_PHOTO_URL = (
    "https://gb-prod-temp.s3.eu-west-1.amazonaws.com/imageId-FAKEIMAGE0000"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20261006T101530Z"
    "&X-Amz-SignedHeaders=host&X-Amz-Credential=FAKECRED%2Ffake"
    "&X-Amz-Expires=259200&X-Amz-Signature=fakesignature0000"
)
FAKE_PHOTO_EXPIRES = "2026-10-09T10:15:30+00:00"


def tile(
    shipment_id: str,
    status: str,
    note: str | None = "Parcel was left in letterbox",
    *,
    order_id: str = ORDER_ID,
    line_item: str = "lineitem0001",
    placed: str = "2 October 2026",
    title: str = "Example Widget, Blue",
    package_id: str = "1",
    placed_anchor: str = "Order placed",
    href: str | None = None,
) -> str:
    """One order line, labelled the way the page labels it."""
    tail = f", {status}" + (f", {note}" if note else "") if status else ""
    label = escape(f"{title}, {placed_anchor} {placed}{tail}")
    href = href or (
        "/-/en/your-orders/pop?ref=ppx_test&orderId="
        + order_id
        + "&lineItemId="
        + line_item
        + "&shipmentId="
        + shipment_id
        + "&packageId="
        + package_id
        + "&asin=B000TEST00"
    )
    return (
        f'<li><a class="item-card__link" href="{escape(href)}" '
        f'aria-label="{label}"></a></li>'
    )


def orders_page(*tiles: str) -> str:
    """An orders page holding the given order lines."""
    return "<html><body><ul>" + "".join(tiles) + "</ul></body></html>"


def pop_page(track_path: str | None = None) -> str:
    """An order-line page, with or without a "Track package" link."""
    link = (
        f'<a href="{escape(track_path)}" class="a-touch-link">Track package</a>'
        if track_path
        else "<p>This order has multiple shipments.</p>"
    )
    return f"<html><body>{link}</body></html>"


def track_path(shipment_id: str, order_id: str = ORDER_ID) -> str:
    """The ship-track path a pop page links to."""
    return (
        "/-/en/gp/your-account/ship-track?itemId=item0001&ref=ppx_test"
        f"&packageIndex=0&orderId={order_id}&shipmentId={shipment_id}"
    )


def progress_tracker_path(shipment_id: str, order_id: str = ORDER_ID) -> str:
    """The tracking page an amazon.fr in-flight order line links to directly."""
    return (
        "/progress-tracker/package?ref=ppx_test&orderId="
        f"{order_id}&_encoding=UTF8&shipmentId={shipment_id}"
        "&packageIndex=0&vt=NOTIFICATIONS"
    )


# A delivered line's pop page links this survey ahead of the real tracking page.
FEEDBACK_PATH = (
    "/progress-tracker/package/delivery-information/delivery-feedback"
    f"?ref=ppx_test&orderId={ORDER_ID}&shipmentId={DELIVERED_SHIPMENT}&packageIndex=0"
)


def track_page(
    *,
    tracking_id: str | None = ACTIVE_CODE,
    carrier: str | None = "DRAGONFLY",
    carrier_header: str | None = None,
    tracking_label: str = "Tracking ID:",
    milestone: str | None = "DELIVERED",
    short_status: str | None = None,
    events: list[tuple[str, str, str, str]] | None = None,
    timezone: str | None = "Europe/Amsterdam",
    state: bool = True,
    delivery_photo: str | None = None,
) -> str:
    """A ship-track page. ``events`` are ``(date, time, message, location)``."""
    parts = ["<html><body>"]
    if carrier_header is None and carrier:
        carrier_header = f"Delivery By {carrier}"
    if carrier_header:
        parts.append(
            '<section class="pt-card delivery-card"><div class="pt-delivery-card-wrapper">'
            f'<div><h3 class="a-spacing-small">{carrier_header}</h3>'
        )
    if tracking_id:
        parts.append(
            f'<div class="pt-delivery-card-trackingId">{tracking_label} {tracking_id}</div>'
        )
    if delivery_photo:
        # The visible images are lazy-loaded (real URL in data-src, placeholder
        # in src); the noscript twin carries it plainly.
        parts.append(
            '<span class="a-declarative" data-action="pt-delivery-card-pod-popup"'
            """ data-pt-delivery-card-pod-popup='{"header":"Bezorgfoto"}'>"""
            '<img alt="" class="photo-on-delivery-img-thumb a-lazy-loaded"'
            f' data-src="{escape(delivery_photo, quote=True)}"'
            ' src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="/>'
            f'<noscript><img alt="" src="{escape(delivery_photo, quote=True)}"/></noscript>'
            "</span>"
            '<div class="a-popover-preload" id="a-popover-pt-delivery-card-pod-popup-content">'
            '<img alt="" class="photo-on-delivery-img a-lazy-loaded"'
            f' data-src="{escape(delivery_photo, quote=True)}" src="data:image/gif;base64,R0lGODlh"/>'
            f'<noscript><img alt="" src="{escape(delivery_photo, quote=True)}"/></noscript>'
            "</div>"
        )
    last_date = None
    for day, moment, message, location in events or []:
        if day != last_date:
            parts.append(f'<span class="tracking-event-date">{day}</span>')
            last_date = day
        parts.append(f'<span class="tracking-event-time">{moment}</span>')
        parts.append(f'<span class="tracking-event-message">{escape(message)}</span>')
        parts.append(f'<span class="tracking-event-location">{escape(location)}</span>')
    if state:
        payload: dict = {
            "orderId": ORDER_ID,
            "customerId": FAKE_CUSTOMER,
            "hereMapsApiKey": FAKE_MAPS_KEY,
            "progressTracker": {"lastReachedMilestone": milestone},
        }
        if timezone:
            payload["timezone"] = timezone
        if short_status:
            payload["shortStatus"] = short_status
        if tracking_id:
            payload["trackingId"] = tracking_id
        parts.append(
            '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;page-state&quot;}">'
            + json.dumps(payload)
            + "</script>"
        )
    parts.append("</body></html>")
    return "".join(parts)


DELIVERED_EVENTS = [
    ("Saturday, 3 October", "10:36 am", "Delivered to customer", ""),
    ("Saturday, 3 October", "8:05 am", "Out for delivery", ""),
    (
        "Saturday, 3 October",
        "2:55 am",
        "Parcel arrived at a carrier facility",
        "Testville, NL",
    ),
    ("Friday, 2 October", "", "Package left the courier facility", ""),
]


def event(timestamp: str, message: str, location: str | None = None) -> dict:
    """One timeline entry as the parser emits it."""
    return {"timestamp": timestamp, "message": message, "location": location}


def delivered_record(code: str = DELIVERED_CODE) -> dict:
    """A shipment record for a delivered parcel with a tracking page."""
    return {
        "domain": COUNTRY,
        "order_id": ORDER_ID,
        "shipment_id": DELIVERED_SHIPMENT,
        "package_id": "1",
        "barcode_source": "tracking_id",
        "tracking_id": code,
        "carrier_code": "DRAGONFLY",
        "order_status": "Delivered 3 October",
        "order_status_note": "Parcel was left in letterbox",
        "delivered_on": "2026-10-03",
        "milestone": "DELIVERED",
        "events": [
            event("2026-10-03T10:36:00+02:00", "Delivered to customer"),
            event("2026-10-03T08:05:00+02:00", "Out for delivery"),
            event(
                "2026-10-03T02:55:00+02:00",
                "Parcel arrived at a carrier facility",
                "Testville, NL",
            ),
            event("2026-10-02T00:00:00+02:00", "Package left the courier facility"),
        ],
        "items": ["Example Widget, Blue"],
        "pop_path": "/-/en/your-orders/pop?orderId=" + ORDER_ID,
        "track_path": track_path(DELIVERED_SHIPMENT),
        "page_state": {"customerId": FAKE_CUSTOMER},
        "delivery_photo_url": FAKE_PHOTO_URL,
        "delivery_photo_expires": FAKE_PHOTO_EXPIRES,
    }


def active_record(code: str = ACTIVE_CODE) -> dict:
    """A shipment record for a parcel that is out for delivery."""
    record = delivered_record(code)
    record.update(
        {
            "shipment_id": ACTIVE_SHIPMENT,
            "order_status": "Arriving today",
            "order_status_note": None,
            "delivered_on": None,
            "milestone": "IN_PROGRESS",
            "track_path": track_path(ACTIVE_SHIPMENT),
            # The photo only exists once Amazon's own fleet has delivered.
            "delivery_photo_url": None,
            "delivery_photo_expires": None,
        }
    )
    record["events"] = [
        event("2026-10-03T08:05:00+02:00", "Out for delivery"),
        event("2026-10-03T02:55:00+02:00", "Parcel arrived at a carrier facility"),
    ]
    return record


def in_transit_record(code: str = ACTIVE_CODE) -> dict:
    """A shipment record that has reached a carrier facility only."""
    record = active_record(code)
    record["events"] = record["events"][1:]
    return record


def untracked_record() -> dict:
    """A shipment whose order line has no tracking page (not yet dispatched)."""
    return {
        "domain": COUNTRY,
        "order_id": ORDER_ID,
        "shipment_id": "SHIPnotrack1",
        "package_id": "1",
        "barcode_source": "shipment_key",
        "tracking_id": None,
        "carrier_code": None,
        "order_status": "Not Yet Dispatched",
        "order_status_note": None,
        "delivered_on": None,
        "milestone": None,
        "events": [],
        "items": ["Example Widget, Blue"],
        "pop_path": "/-/en/your-orders/pop?orderId=" + ORDER_ID,
        "track_path": None,
        "page_state": {},
    }


BUSINESS_TOKEN = "FAKECSRFTOKEN" + "0" * 91


def business_shell(*, legal_entity: str | None = "A00FAKELEGAL00") -> str:
    """An Amazon Business orders page: no order line, only what the list call needs."""
    entity = (
        '<div id="hiddenFilterButtonListOrderedAndPaidBy" class="aok-hidden">'
        f'<span id="button_yoAllOrders-{legal_entity}" class="selectedFilterButton"></span></div>'
        if legal_entity
        else ""
    )
    return (
        "<html><head>"
        f'<meta name="anti-csrftoken-a2z" content="{BUSINESS_TOKEN}" id="ab-your-orders-anticsrf-token">'
        "</head><body>"
        '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;yourOrdersStateData&quot;}">'
        '{"marketplaceId":"fakeMarketId","businessId":"fakeBizId0",'
        '"loggedInCustomerId":"fakeCustomerId0000","sessionId":"fakeSessn"}</script>'
        '<input type="hidden" name="originalRequestId" value="0FAKEREQUESTID000000" id="originalRequestId">'
        f'{entity}<div id="yourOrderHistorySection" class="a-row"></div>'
        "</body></html>"
    )


def business_tracker_path(shipment_id: str, order_id: str = ORDER_ID) -> str:
    """Where an in-flight Business card links: its tracking page."""
    return (
        "/progress-tracker/package/ref=ab_ppx_yo_mob_b_track_package?_encoding=UTF8"
        f"&orderId={order_id}&itemId=item0001&shipmentId={shipment_id}&vt=YOUR_ORDERS"
    )


def business_order_path(order_id: str = ORDER_ID) -> str:
    """Where a delivered Business card links: its order page."""
    return f"/gp/aw/ya/ref=ab_ppx_yo_mob_b_od?ie=UTF8&ac=X&oid={order_id}"


def business_card(
    href: str,
    status: str,
    *,
    title: str = "Example item 1",
    note: str | None = None,
    cancelled: bool = False,
) -> str:
    """One card of the Business order list, in the page's own markup."""
    kind = "cancelledDeliveryCard" if cancelled else "nonCancelledDeliveryCard"
    secondary = (
        '<p class="secondaryMessage">\n <span class="a-color-secondary">\n'
        f" {escape(note)}\n </span>\n </p>\n"
        if note
        else ""
    )
    return (
        f'<a href="{escape(href)}" class="a-touch-link a-box {kind}"><div class="a-box-inner">'
        '<div class="a-row displayFlex"><div class="a-column a-span9"><div class="a-section">'
        '<p class="primaryMessage">\n <span class="a-color-success a-text-bold">\n'
        f" {status}\n </span>\n </p>\n{secondary}"
        '<p class="itemTitle">\n <span class="a-truncate itemTitleTruncate a-size-base">'
        f'<span class="a-truncate-full">{escape(title)}</span>'
        f'<span class="a-truncate-cut a-hidden" aria-hidden="true">{escape(title)}</span></span>\n </p>'
        "</div></div></div></div></a>"
    )


def business_history(
    *cards: str, count: int | None = None, exception: bool = False
) -> str:
    """The fragment the Business order-list call returns."""
    rendered = len(cards) if count is None else count
    return (
        '<div id="pageAsinsSection" class="a-section"><div id="pageAsins" class="a-section"></div></div>'
        '<div id="yourOrderHistorySection" class="a-row">'
        f'<p id="deliveryHistoryException" data-isexception="{str(exception).lower()}" class="aok-hidden"></p>'
        f'<p id="noOfDeliveryCards" data-noofdeliverycardsrendered="{rendered}" class="aok-hidden"></p>'
        + "".join(cards)
        + "</div>"
    )

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
) -> str:
    """One order line, labelled the way the page labels it."""
    tail = f", {status}" + (f", {note}" if note else "") if status else ""
    label = escape(f"{title}, Order placed {placed}{tail}")
    href = (
        "/-/en/your-orders/pop?ref=ppx_test&orderId=" + order_id
        + "&lineItemId=" + line_item + "&shipmentId=" + shipment_id
        + "&packageId=" + package_id + "&asin=B000TEST00"
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


def track_page(
    *,
    tracking_id: str | None = ACTIVE_CODE,
    carrier: str | None = "DRAGONFLY",
    milestone: str | None = "DELIVERED",
    events: list[tuple[str, str, str, str]] | None = None,
    timezone: str | None = "Europe/Amsterdam",
    state: bool = True,
) -> str:
    """A ship-track page. ``events`` are ``(date, time, message, location)``."""
    parts = ["<html><body>"]
    if carrier:
        parts.append(f"<h3>Delivery By {carrier}</h3>")
    if tracking_id:
        parts.append(f'<div class="pt-delivery-card-trackingId">Tracking ID: {tracking_id}</div>')
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
    ("Saturday, 3 October", "2:55 am", "Parcel arrived at a carrier facility", "Testville, NL"),
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
            event("2026-10-03T02:55:00+02:00", "Parcel arrived at a carrier facility", "Testville, NL"),
            event("2026-10-02T00:00:00+02:00", "Package left the courier facility"),
        ],
        "items": ["Example Widget, Blue"],
        "pop_path": "/-/en/your-orders/pop?orderId=" + ORDER_ID,
        "track_path": track_path(DELIVERED_SHIPMENT),
        "page_state": {"customerId": FAKE_CUSTOMER},
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

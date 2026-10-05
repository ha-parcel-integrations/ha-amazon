"""Tests for the HTML page parsers."""
from datetime import date

import pytest

from custom_components.amazon_orders.account.pages import (
    _day_month,
    build_record,
    looks_signed_out,
    parse_order_tiles,
    parse_track_link,
    parse_track_page,
)

from ..payloads import (
    ACTIVE_CODE,
    ACTIVE_SHIPMENT,
    COUNTRY,
    DELIVERED_EVENTS,
    DELIVERED_SHIPMENT,
    FAKE_MAPS_KEY,
    ORDER_ID,
    orders_page,
    pop_page,
    tile,
    track_page,
    track_path,
)

TODAY = date(2026, 10, 5)

# ---------------------------------------------------------------------------
# order lines
# ---------------------------------------------------------------------------


def test_delivered_tile_is_parsed_from_its_label():
    page = orders_page(
        tile(DELIVERED_SHIPMENT, "Delivered 3 October", "Left with a neighbour")
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.order_id == ORDER_ID
    assert parsed.shipment_id == DELIVERED_SHIPMENT
    assert parsed.package_id == "1"
    assert parsed.title == "Example Widget, Blue"
    assert parsed.status_text == "Delivered 3 October"
    assert parsed.status_note == "Left with a neighbour"
    assert parsed.placed_on == date(2026, 10, 2)
    assert parsed.delivered_on == date(2026, 10, 3)
    assert parsed.delivered is True
    assert parsed.key == (ORDER_ID, DELIVERED_SHIPMENT, "1")
    assert parsed.pop_path.startswith("/-/en/your-orders/pop?")


def test_delivery_after_new_year_rolls_the_year_on():
    page = orders_page(
        tile(DELIVERED_SHIPMENT, "Delivered 2 January", placed="30 December 2026")
    )
    (parsed,) = parse_order_tiles(page, date(2027, 1, 5))
    assert parsed.delivered_on == date(2027, 1, 2)


def test_in_flight_tile_has_no_delivery_date():
    page = orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None))
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.status_text == "Arriving today"
    assert parsed.status_note is None
    assert parsed.delivered is False
    assert parsed.delivered_on is None


def test_label_without_a_placed_date_still_yields_its_status():
    label_less = (
        '<a class="item-card__link" '
        'href="/-/en/your-orders/pop?orderId=000-0000009-0000009&amp;shipmentId=S1" '
        'aria-label="Widget, Delivered 3 October"></a>'
    )
    (parsed,) = parse_order_tiles(label_less, TODAY)
    assert parsed.placed_on is None
    assert parsed.title == "Widget"
    assert parsed.status_text == "Delivered 3 October"
    assert parsed.delivered_on is not None


def test_unanchored_label_with_commas_in_the_title_finds_the_status():
    page = (
        '<a class="item-card__link" '
        'href="/-/en/your-orders/pop?orderId=000-0000009-0000009" '
        'aria-label="Cable, 1 m, 2 items, Delivered on 19 September, Left at door"></a>'
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.title == "Cable, 1 m, 2 items"
    assert parsed.status_text == "Delivered on 19 September"
    assert parsed.status_note == "Left at door"
    assert parsed.delivered_on is not None
    assert (parsed.delivered_on.month, parsed.delivered_on.day) == (9, 19)


def test_unanchored_label_without_a_known_status_keeps_the_whole_title():
    page = (
        '<a class="item-card__link" '
        'href="/-/en/your-orders/pop?orderId=000-0000009-0000009" '
        'aria-label="Widget, something new"></a>'
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.title == "Widget, something new"
    assert parsed.status_text == ""
    assert parsed.delivered_on is None


def test_tile_without_an_order_id_is_ignored():
    page = (
        '<a class="item-card__link" href="/-/en/your-orders/pop?x=1" '
        'aria-label="Widget"></a>'
    )
    assert parse_order_tiles(page, TODAY) == []


def test_title_with_commas_and_entities_survives():
    page = orders_page(
        tile(ACTIVE_SHIPMENT, "Arriving today", None, title="Cable, 1 m & plug, 2 items")
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.title == "Cable, 1 m & plug, 2 items"
    assert parsed.status_text == "Arriving today"


def test_page_without_tiles_is_empty():
    assert parse_order_tiles("<html></html>", TODAY) == []


# ---------------------------------------------------------------------------
# day / month resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,reference,forward,expected",
    [
        ("3 October", TODAY, False, date(2026, 10, 3)),
        ("30 December", TODAY, False, date(2025, 12, 30)),  # would be the future
        ("2 January", date(2026, 12, 30), True, date(2027, 1, 2)),
        ("2 October 2020", TODAY, False, date(2020, 10, 2)),
        ("Saturday, 3 October", TODAY, False, date(2026, 10, 3)),
        ("31 February", TODAY, False, None),
        ("3 Oktobr", TODAY, False, None),
        ("no date here", TODAY, False, None),
    ],
)
def test_day_month(text, reference, forward, expected):
    assert _day_month(text, reference, forward=forward) == expected


# ---------------------------------------------------------------------------
# order-line page
# ---------------------------------------------------------------------------


def test_track_link_is_found_and_unescaped():
    path = track_path(DELIVERED_SHIPMENT)
    assert parse_track_link(pop_page(path)) == path


def test_track_link_missing_is_none():
    assert parse_track_link(pop_page(None)) is None


def test_signed_out_page_is_recognised():
    assert looks_signed_out('<form><input type="password" name="password"></form>')
    assert not looks_signed_out(orders_page())


# ---------------------------------------------------------------------------
# ship-track page
# ---------------------------------------------------------------------------


def test_track_page_reads_state_carrier_and_timeline():
    info = parse_track_page(
        track_page(events=DELIVERED_EVENTS, tracking_id=ACTIVE_CODE), TODAY
    )
    assert info.tracking_id == ACTIVE_CODE
    assert info.carrier_code == "DRAGONFLY"
    assert info.timezone == "Europe/Amsterdam"
    assert info.state["progressTracker"] == {"lastReachedMilestone": "DELIVERED"}
    assert [e["message"] for e in info.events] == [
        "Delivered to customer",
        "Out for delivery",
        "Parcel arrived at a carrier facility",
        "Package left the courier facility",
    ]
    assert info.events[0]["timestamp"] == "2026-10-03T10:36:00+02:00"
    assert info.events[2]["location"] == "Testville, NL"
    assert info.events[0]["location"] is None
    # No time given: the day's midnight stands in.
    assert info.events[3]["timestamp"] == "2026-10-02T00:00:00+02:00"


def test_embedded_state_drops_the_third_party_map_key():
    info = parse_track_page(track_page(), TODAY)
    assert "hereMapsApiKey" not in info.state
    assert FAKE_MAPS_KEY not in str(info.state)
    assert "customerId" in info.state


def test_tracking_id_falls_back_to_the_visible_text():
    info = parse_track_page(track_page(state=False), TODAY)
    assert info.tracking_id == ACTIVE_CODE
    assert info.state == {}
    assert info.timezone is None


def test_page_without_any_tracking_id():
    info = parse_track_page(track_page(tracking_id=None, carrier=None, state=False), TODAY)
    assert info.tracking_id is None
    assert info.carrier_code is None


def test_carrier_header_is_kept_when_its_wording_is_not_read():
    info = parse_track_page(track_page(carrier_header="Bezorgd door Testcarrier"), TODAY)
    assert info.carrier_code is None
    assert info.carrier_header == "Bezorgd door Testcarrier"

    info = parse_track_page(track_page(carrier="DHL_CONNECT"), TODAY)
    assert info.carrier_code == "DHL_CONNECT"
    assert info.carrier_header == "Delivery By DHL_CONNECT"


def test_unknown_timezone_leaves_timestamps_naive():
    info = parse_track_page(
        track_page(events=DELIVERED_EVENTS[:1], timezone="Nowhere/Land"), TODAY
    )
    assert info.timezone == "Nowhere/Land"
    assert info.events[0]["timestamp"] == "2026-10-03T10:36:00"


def test_broken_state_json_is_ignored():
    page = (
        '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;page-state&quot;}">'
        "{not json}</script>"
    )
    assert parse_track_page(page, TODAY).state == {}


def test_minimal_state_objects_are_read():
    page = '<script type="a-state" data-a-state="page-state">{"a": 1}</script>'
    assert parse_track_page(page, TODAY).state == {"a": 1}
    page = '<script type="a-state" data-a-state="page-state">{}</script>'
    assert parse_track_page(page, TODAY).state == {}


def test_timezone_that_is_not_text_is_ignored():
    page = (
        '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;page-state&quot;}">'
        '{"timezone": 5}</script>'
    )
    assert parse_track_page(page, TODAY).timezone is None


def test_events_without_a_date_or_message_are_dropped():
    page = (
        '<span class="tracking-event-time">1:00 pm</span>'
        '<span class="tracking-event-message">Orphan</span>'
        '<span class="tracking-event-location"></span>'
        '<span class="tracking-event-date">Friday, 2 October</span>'
        '<span class="tracking-event-time">garbled</span>'
        '<span class="tracking-event-message"></span>'
        '<span class="tracking-event-location"></span>'
        '<span class="tracking-event-time">2:00 pm</span>'
        '<span class="tracking-event-message">Out for delivery</span>'
        '<span class="tracking-event-location"></span>'
    )
    events = parse_track_page(page, TODAY).events
    assert [e["message"] for e in events] == ["Out for delivery"]


def test_stray_message_without_a_time_is_ignored():
    page = (
        '<span class="tracking-event-date">Friday, 2 October</span>'
        '<span class="tracking-event-message">No time span before me</span>'
        '<span class="tracking-event-location">Somewhere</span>'
    )
    assert parse_track_page(page, TODAY).events == []


# ---------------------------------------------------------------------------
# record assembly
# ---------------------------------------------------------------------------


def _tiles(status="Delivered 3 October"):
    return parse_order_tiles(orders_page(tile(DELIVERED_SHIPMENT, status)), TODAY)


def test_record_with_a_tracking_page():
    path = track_path(DELIVERED_SHIPMENT)
    info = parse_track_page(track_page(events=DELIVERED_EVENTS), TODAY)
    record = build_record(COUNTRY, _tiles(), path, info)
    assert record["barcode_source"] == "tracking_id"
    assert record["tracking_id"] == ACTIVE_CODE
    assert record["carrier_code"] == "DRAGONFLY"
    assert record["milestone"] == "DELIVERED"
    assert record["delivered_on"] == "2026-10-03"
    assert record["order_status"] == "Delivered 3 October"
    assert record["track_path"] == path
    assert record["items"] == ["Example Widget, Blue"]
    assert len(record["events"]) == 4


def test_record_without_a_tracking_page_is_flagged():
    record = build_record(COUNTRY, _tiles("Not Yet Dispatched"), None, None)
    assert record["barcode_source"] == "shipment_key"
    assert record["tracking_id"] is None
    assert record["milestone"] is None
    assert record["events"] == []
    assert record["delivered_on"] is None
    assert record["page_state"] == {}


def test_record_with_a_page_that_has_no_progress_tracker():
    info = parse_track_page(track_page(state=False), TODAY)
    record = build_record(COUNTRY, _tiles(), track_path(DELIVERED_SHIPMENT), info)
    assert record["milestone"] is None


def _bare_tile(href: str, label: str = "Widget, Order placed 2 October 2026") -> str:
    return f'<a class="item-card__link" href="{href}" aria-label="{label}"></a>'


def test_undispatched_line_without_a_shipment_is_keyed_on_its_line():
    page = orders_page(
        _bare_tile(
            "/-/en/your-orders/pop?orderId=000-0000002-0000002&lineItemId=lineitem0009"
        )
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.shipment_id == ""
    assert parsed.shipment_key == "000-0000002-0000002-lineitem0009"
    assert parsed.key == ("000-0000002-0000002", parsed.shipment_key, "1")


def test_undispatched_line_without_any_ids_is_keyed_on_its_position():
    page = orders_page(
        _bare_tile("/-/en/your-orders/pop?orderID=000-0000002-0000002"),
        _bare_tile("/-/en/your-orders/pop?orderID=000-0000002-0000002"),
    )
    first, second = parse_order_tiles(page, TODAY)
    assert first.shipment_key == "000-0000002-0000002-0"
    assert second.shipment_key == "000-0000002-0000002-1"


def test_order_id_is_found_in_the_path_when_not_in_the_query():
    page = orders_page(_bare_tile("/-/en/your-orders/order-details/000-0000003-0000003"))
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.order_id == "000-0000003-0000003"


def test_shipped_line_keeps_its_shipment_id_as_key():
    (parsed,) = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None)), TODAY
    )
    assert parsed.shipment_key == ACTIVE_SHIPMENT


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Zugestellt am 3. Oktober", date(2026, 10, 3)),
        ("Entregado el 3 de octubre", date(2026, 10, 3)),
        ("Livré le 3 oct.", date(2026, 10, 3)),
        ("Delivered 3 Oct. 2025", date(2025, 10, 3)),
        ("Bezorgd 3 oktober", date(2026, 10, 3)),
        ("Dostarczono 3 października", date(2026, 10, 3)),
        ("Zugestellt am 3. März", date(2026, 3, 3)),
    ],
)
def test_day_month_shapes_in_other_languages(text, expected):
    assert _day_month(text, TODAY) == expected


def test_cancelled_and_returned_lines_are_not_listed():
    page = orders_page(
        tile("SHIPcancel1", "Cancelled", None),
        tile("SHIPreturn1", "Return complete", None),
        tile("SHIPrefund1", "Refund for this return", None),
        tile(ACTIVE_SHIPMENT, "Arriving today", None),
    )
    assert [t.shipment_id for t in parse_order_tiles(page, TODAY)] == [ACTIVE_SHIPMENT]


def test_a_german_delivered_line_is_delivered_and_dated(caplog):
    page = orders_page(
        tile(
            DELIVERED_SHIPMENT,
            "Zugestellt am 3. Oktober",
            None,
            placed="2 Oktober 2026",
        )
    )
    # "Order placed" is English-only, so the segment is found by its wording.
    page = page.replace("Order placed 2 Oktober 2026", "Bestellt am 2. Oktober 2026")
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.delivered is True
    assert parsed.delivered_on == date(2026, 10, 3)
    assert caplog.text.count("confirm it is right") == 1

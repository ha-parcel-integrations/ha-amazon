"""Tests for the HTML page parsers."""

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from custom_components.amazon_orders.account.pages import (
    _day_month,
    arrival_days,
    build_record,
    business_card_count,
    business_cards_found,
    business_history_failed,
    is_track_page_path,
    looks_signed_out,
    page_timezone,
    parse_business_cards,
    parse_business_shell,
    parse_delivery_photo,
    parse_order_tiles,
    parse_track_link,
)
from custom_components.amazon_orders.account.pages import (
    delivery_window as _delivery_window,
)
from custom_components.amazon_orders.account.pages import (
    parse_track_page as _parse_track_page,
)
from custom_components.amazon_orders.account.parcels import normalize_parcel
from custom_components.amazon_orders.const import ParcelStatus

from ..payloads import (
    ACTIVE_CODE,
    ACTIVE_SHIPMENT,
    BUSINESS_TOKEN,
    COUNTRY,
    DELIVERED_EVENTS,
    DELIVERED_SHIPMENT,
    FAKE_MAP_TRACKING,
    FAKE_MAPS_KEY,
    FAKE_PHOTO_URL,
    FEEDBACK_PATH,
    ORDER_ID,
    business_card,
    business_history,
    business_order_path,
    business_shell,
    business_tracker_path,
    orders_page,
    pop_page,
    progress_tracker_path,
    tile,
    track_page,
    track_path,
)

TODAY = date(2026, 10, 5)


def _resolved(name: object) -> ZoneInfo | None:
    """What the client resolves a page's zone to before parsing."""
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_track_page(page: str, today: date):
    return _parse_track_page(page, today, _resolved(page_timezone(page)))


def delivery_window(state: dict, read_at: datetime):
    return _delivery_window(state, read_at, _resolved(state.get("timezone")))


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


def test_a_us_line_reads_its_month_first_dates(caplog):
    # amazon.com writes both dates month-first: "Order placed November 8, 2026,
    # Delivered November 9" (ha-amazon #32).
    page = orders_page(
        tile(
            DELIVERED_SHIPMENT,
            "Delivered November 9",
            None,
            placed="November 8, 2026",
        )
    )
    (parsed,) = parse_order_tiles(page, date(2026, 11, 10))
    assert parsed.title == "Example Widget, Blue"
    assert parsed.status_text == "Delivered November 9"
    assert parsed.placed_on == date(2026, 11, 8)
    assert parsed.delivered_on == date(2026, 11, 9)
    assert "gave no date we could read" not in caplog.text


def test_a_us_line_delivered_before_new_year_keeps_the_placed_year():
    page = orders_page(
        tile(
            DELIVERED_SHIPMENT, "Delivered January 2", None, placed="December 30, 2026"
        )
    )
    (parsed,) = parse_order_tiles(page, date(2027, 1, 5))
    assert parsed.delivered_on == date(2027, 1, 2)


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


def test_a_belgian_line_delivered_today_is_dated_today(caplog):
    page = orders_page(tile(DELIVERED_SHIPMENT, "Vandaag bezorgd", None))
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.delivered is True
    assert parsed.delivered_on == TODAY
    assert caplog.text == ""


def test_a_belgian_line_due_tomorrow_reads_as_in_transit(caplog):
    page = (
        '<a class="item-card__link" '
        'href="/-/nl/your-orders/pop?orderId=000-0000009-0000009&amp;shipmentId=S1" '
        'aria-label="Widget, Wordt morgen bezorgd"></a>'
    )
    (parsed,) = parse_order_tiles(page, TODAY)
    assert parsed.title == "Widget"
    assert parsed.status_text == "Wordt morgen bezorgd"
    assert parsed.delivered is False
    assert "confirm it is right" not in caplog.text


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
        tile(
            ACTIVE_SHIPMENT, "Arriving today", None, title="Cable, 1 m & plug, 2 items"
        )
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
        # amazon.com's month-first shapes.
        ("October 3", TODAY, False, date(2026, 10, 3)),
        ("December 30", TODAY, False, date(2025, 12, 30)),  # would be the future
        ("January 2", date(2026, 12, 30), True, date(2027, 1, 2)),
        ("Oct 2, 2020", TODAY, False, date(2020, 10, 2)),
        ("Delivered Nov. 9", date(2026, 11, 10), False, date(2026, 11, 9)),
        ("February 31", TODAY, False, None),
        ("Delivered to letterbox", TODAY, False, None),
        ("Cable, 1 m, 2 items", TODAY, False, None),
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


def test_delivery_photo_is_read_from_the_lazy_data_src():
    info = parse_track_page(track_page(delivery_photo=FAKE_PHOTO_URL), TODAY)
    assert info.delivery_photo_url == FAKE_PHOTO_URL


def test_delivery_photo_is_none_without_the_card():
    assert parse_track_page(track_page(), TODAY).delivery_photo_url is None


def test_delivery_photo_falls_back_to_the_noscript_image():
    escaped = FAKE_PHOTO_URL.replace("&", "&amp;")
    page = (
        '<html><body><div class="a-popover-preload" '
        'id="a-popover-pt-delivery-card-pod-popup-content">'
        f'<noscript><img alt="" src="{escaped}"/></noscript></div></body></html>'
    )
    assert parse_delivery_photo(page) == FAKE_PHOTO_URL


def test_tracking_id_tolerates_the_dutch_label():
    page = track_page(state=False).replace("Tracking ID:", "Tracking-id:")
    assert parse_track_page(page, TODAY).tracking_id == ACTIVE_CODE


def test_embedded_state_drops_the_third_party_map_key():
    info = parse_track_page(track_page(), TODAY)
    assert "hereMapsApiKey" not in info.state
    assert FAKE_MAPS_KEY not in str(info.state)
    assert "customerId" in info.state


@pytest.mark.parametrize(
    ("callout", "stops"),
    [
        ("8 Stopps entfernt", 8),
        ("1 Stopp entfernt", 1),
        (" 12  Stopps entfernt ", 12),
        ("5 stops away", 5),
        ("1 stop away", 1),
        ("Du bist der nächste Halt", 0),
        (" du bist der  nächste Halt. ", 0),
        ("Sie sind der nächste Halt", 0),
        ("You’re the next stop!", 0),
        ("You are the next stop", 0),
    ],
)
def test_stops_away_is_read_from_the_live_map(callout, stops):
    page = track_page(map_tracking={**FAKE_MAP_TRACKING, "calloutMessage": callout})
    info = parse_track_page(page, TODAY)
    record = build_record(COUNTRY, _tiles(), track_path(ACTIVE_SHIPMENT), info)
    assert record["stops_away"] == stops


@pytest.mark.parametrize("map_tracking", [None, {}, {"calloutMessage": ""}, "x"])
def test_stops_away_is_none_without_a_count(map_tracking, caplog):
    info = parse_track_page(track_page(map_tracking=map_tracking), TODAY)
    assert info.stops_away is None
    assert "stop count" not in caplog.text


def test_unread_stop_wording_warns_once_without_its_digits(caplog):
    page = track_page(map_tracking={"calloutMessage": "Sie sind in 3 Stopps dran"})
    assert parse_track_page(page, TODAY).stops_away is None
    assert parse_track_page(page, TODAY).stops_away is None
    assert caplog.text.count('stop count as "Sie sind in # Stopps dran"') == 1


def test_next_stop_wording_does_not_warn(caplog):
    page = track_page(map_tracking={"calloutMessage": "Du bist der nächste Halt"})
    assert parse_track_page(page, TODAY).stops_away == 0
    assert "stop count" not in caplog.text


def test_record_without_a_tracking_page_has_no_stop_count():
    assert build_record(COUNTRY, _tiles(), None, None)["stops_away"] is None


def test_record_keeps_its_order_line_ids_for_the_dispatch_rename():
    unshipped = parse_order_tiles(orders_page(tile("", "Not yet dispatched")), TODAY)
    record = build_record(COUNTRY, unshipped, None, None)
    assert record["shipment_id"] == f"{ORDER_ID}-lineitem0001"
    assert record["line_item_ids"] == ["lineitem0001"]


def test_embedded_state_drops_the_live_map_and_its_coordinates():
    info = parse_track_page(track_page(map_tracking=FAKE_MAP_TRACKING), TODAY)
    record = build_record(COUNTRY, _tiles(), track_path(DELIVERED_SHIPMENT), info)
    assert "mapTracking" not in info.state
    assert "52.0001" not in str(record)


def test_tracking_id_falls_back_to_the_visible_text():
    info = parse_track_page(track_page(state=False), TODAY)
    assert info.tracking_id == ACTIVE_CODE
    assert info.state == {}
    assert info.timezone is None


def test_page_without_any_tracking_id():
    info = parse_track_page(
        track_page(tracking_id=None, carrier=None, state=False), TODAY
    )
    assert info.tracking_id is None
    assert info.carrier_code is None


def test_carrier_header_is_kept_when_its_wording_is_not_read():
    info = parse_track_page(
        track_page(carrier_header="Bezorgd door Testcarrier"), TODAY
    )
    assert info.carrier_code is None
    assert info.carrier_header == "Bezorgd door Testcarrier"

    info = parse_track_page(track_page(carrier="DHL_CONNECT"), TODAY)
    assert info.carrier_code == "DHL_CONNECT"
    assert info.carrier_header == "Delivery By DHL_CONNECT"


def test_enviado_con_amazon_names_amazon_logistics():
    # #48: the amazon.es header for a parcel Amazon delivers itself.
    info = parse_track_page(track_page(carrier_header="Enviado con Amazon"), TODAY)
    assert info.carrier_code == "Amazon"


def test_expedie_avec_amazon_names_amazon_logistics():
    # #58: the amazon.fr header for a parcel Amazon delivers itself.
    info = parse_track_page(track_page(carrier_header="Expédié avec Amazon"), TODAY)
    assert info.carrier_code == "Amazon"


def test_intelcom_is_aliased_to_dragonfly():
    info = parse_track_page(track_page(carrier="Intelcom"), TODAY)
    assert info.carrier_code == "DRAGONFLY"
    assert info.carrier_header == "Delivery By Intelcom"


@pytest.mark.parametrize(
    "header,code",
    [
        ("Bezorging door COLIS_PRIVE_BELU", "COLIS_PRIVE_BELU"),
        ("Levering door Amazon", "Amazon"),
        ("Versendet mit DHL", "DHL_CONNECT"),
        ("Versand durch Amazon", "Amazon"),
        # #41: amazon.de in English writes "by" in lower case.
        ("Delivery by Amazon", "Amazon"),
        ("Delivery by DPDDE", "DPDDE"),
    ],
)
def test_dutch_carrier_headers_are_read(header, code):
    info = parse_track_page(track_page(carrier_header=header), TODAY)
    assert info.carrier_code == code


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


def test_record_never_carries_the_delivery_photo_link():
    """The link opens without signing in, and raw reaches attributes and events."""
    info = parse_track_page(track_page(delivery_photo=FAKE_PHOTO_URL), TODAY)
    record = build_record(COUNTRY, _tiles(), track_path(DELIVERED_SHIPMENT), info)
    assert "s3.eu-west-1.amazonaws.com" not in str(record)


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
    page = orders_page(
        _bare_tile("/-/en/your-orders/order-details/000-0000003-0000003")
    )
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
        # 2026-10-05 is a Monday.
        ("Delivered Saturday", date(2026, 10, 3)),
        ("Delivered Monday", date(2026, 9, 28)),
        ("Delivered yesterday", date(2026, 10, 4)),
        ("Delivered today", date(2026, 10, 5)),
    ],
)
def test_english_relative_delivery_days(text, expected):
    (parsed,) = parse_order_tiles(orders_page(tile(DELIVERED_SHIPMENT, text)), TODAY)
    assert parsed.delivered_on == expected


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
    assert "confirm it is right" not in caplog.text


@pytest.mark.parametrize(
    "prefix", ["Bestellung aufgegeben", "Bestellung aufgegeben am"]
)
def test_a_german_line_on_its_way_is_read_through_its_own_anchor(prefix, caplog):
    # amazon.de labels the line "<title>, Bestellung aufgegeben <D. Monat
    # YYYY>, Zustellung: <Wochentag>" (#27): without the German anchor the
    # status segment came out empty and the parcel was hidden altogether.
    page = orders_page(
        tile(
            ACTIVE_SHIPMENT,
            "Zustellung: Dienstag",
            None,
            placed="2. November 2026",
            placed_anchor=prefix,
        )
    )
    (parsed,) = parse_order_tiles(page, date(2026, 11, 3))
    assert parsed.title == "Example Widget, Blue"
    assert parsed.status_text == "Zustellung: Dienstag"
    assert parsed.placed_on == date(2026, 11, 2)
    assert parsed.delivered is False
    assert "confirm it is right" not in caplog.text


def test_a_german_delivered_line_keeps_its_date_through_the_anchor():
    page = orders_page(
        tile(
            DELIVERED_SHIPMENT,
            "Zugestellt: 2. November",
            None,
            placed="31. Oktober 2026",
            placed_anchor="Bestellung aufgegeben",
        )
    )
    (parsed,) = parse_order_tiles(page, date(2026, 11, 3))
    assert parsed.title == "Example Widget, Blue"
    assert parsed.delivered is True
    assert parsed.delivered_on == date(2026, 11, 2)


def test_dutch_timeline_with_24_hour_times_and_relative_days():
    info = parse_track_page(
        track_page(
            events=[
                ("Vandaag", "11:50", "Wordt vandaag bezorgd", "Antwerpen BE"),
                ("Gisteren", "06:34", "Pakket aangekomen", "Antwerpen BE"),
            ]
        ),
        TODAY,
    )
    assert [e["timestamp"] for e in info.events] == [
        "2026-10-05T11:50:00+02:00",
        "2026-10-04T06:34:00+02:00",
    ]


def test_an_unreadable_timeline_date_drops_its_events_and_warns_once(caplog):
    page = track_page(
        events=[
            ("Someday 12", "11:50", "Out for delivery", ""),
            ("Someday 12", "12:50", "Out for delivery", ""),
        ]
    )
    assert parse_track_page(page, TODAY).events == []
    parse_track_page(page, TODAY)
    assert caplog.text.count('date "Someday #"') == 1


# ---------------------------------------------------------------------------
# amazon.fr
# ---------------------------------------------------------------------------

FR_TODAY = date(2026, 10, 24)
FR_TITLE = "Example item, Blue, 2 pack, 30 cm, XL"


def _fr_tile(shipment: str, status: str, note: str | None = None, **kwargs) -> str:
    return tile(
        shipment,
        status,
        note,
        placed_anchor="Commande effectuée le",
        title=FR_TITLE,
        **kwargs,
    )


def test_french_in_flight_line_is_split_on_its_placed_date(caplog):
    page = orders_page(
        _fr_tile(
            ACTIVE_SHIPMENT,
            "Arrive aujourd'hui dans la tranche horaire suivante : 14:00 - 16:30",
            placed="21 octobre 2026",
            href=progress_tracker_path(ACTIVE_SHIPMENT),
        )
    )
    (parsed,) = parse_order_tiles(page, FR_TODAY)
    assert parsed.title == FR_TITLE
    assert parsed.placed_on == date(2026, 10, 21)
    assert parsed.status_text.startswith("Arrive aujourd'hui")
    assert parsed.shipment_id == ACTIVE_SHIPMENT
    assert parsed.delivered is False
    assert caplog.text == ""


def test_french_delivered_and_cancelled_lines(caplog):
    page = orders_page(
        _fr_tile(
            DELIVERED_SHIPMENT,
            "Livré : 30 septembre",
            "Votre colis a été déposé dans la boîte aux lettres.",
            placed="18 septembre 2026",
        ),
        _fr_tile(
            "SHIPcancel1",
            "Annulé(s)",
            "Votre commande a été annulée.",
            placed="18 septembre 2026",
        ),
        _fr_tile("SHIPtoday01", "Livré aujourd'hui", placed="21 octobre 2026"),
        _fr_tile("SHIPfeb0001", "Livré : 3 février", placed="1 février 2026"),
    )
    delivered, today, february = parse_order_tiles(page, FR_TODAY)
    assert delivered.delivered is True
    assert delivered.placed_on == date(2026, 9, 18)
    assert delivered.delivered_on == date(2026, 9, 30)
    assert (
        delivered.status_note == "Votre colis a été déposé dans la boîte aux lettres."
    )
    assert today.delivered is True
    assert today.delivered_on == FR_TODAY
    assert february.placed_on == date(2026, 2, 1)
    assert february.delivered_on == date(2026, 2, 3)
    assert caplog.text == ""


def test_english_placed_anchor_still_splits_the_title():
    (parsed,) = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None, title=FR_TITLE)),
        TODAY,
    )
    assert parsed.title == FR_TITLE
    assert parsed.placed_on == date(2026, 10, 2)


@pytest.mark.parametrize(
    "path,expected",
    [
        (progress_tracker_path(ACTIVE_SHIPMENT), True),
        ("/progress-tracker/package/ref=ab_ppx_test?orderId=" + ORDER_ID, True),
        (track_path(ACTIVE_SHIPMENT), True),
        ("https://www.amazon.fr/progress-tracker/package?orderId=" + ORDER_ID, True),
        (FEEDBACK_PATH, False),
        ("/your-orders/pop?orderId=" + ORDER_ID, False),
        ("/your-orders/order-details?orderId=" + ORDER_ID, False),
    ],
)
def test_is_track_page_path(path, expected):
    assert is_track_page_path(path) is expected


def test_delivered_pop_page_picks_ship_track_over_the_feedback_survey():
    path = track_path(DELIVERED_SHIPMENT)
    page = (
        f'<html><body><a href="{FEEDBACK_PATH}">Comment s&#39;est passée la'
        f' livraison ?</a><a href="{path}">Suivre votre colis</a></body></html>'
    )
    assert parse_track_link(page) == path


def test_french_tracking_page_reads_carrier_tracking_id_and_timeline(caplog):
    page = track_page(
        tracking_id=ACTIVE_CODE,
        carrier_header="Livraison par Amazon",
        tracking_label="Numéro de suivi :",
        milestone="OUT_FOR_DELIVERY",
        short_status="OUT_FOR_DELIVERY",
        timezone="Europe/Paris",
        events=[
            ("samedi 24 octobre", "13:51", "En cours de livraison", "Ville, FR"),
            (
                "samedi 24 octobre",
                "6:03",
                "Le colis est arrivé à l'agence de livraison finale\n",
                "Ville, FR",
            ),
            (
                "vendredi 23 octobre",
                "",
                "Le colis a quitté les locaux de l'expéditeur",
                "",
            ),
        ],
    )
    info = parse_track_page(page, FR_TODAY)
    assert info.tracking_id == ACTIVE_CODE
    assert info.carrier_code == "Amazon"
    assert info.state["shortStatus"] == "OUT_FOR_DELIVERY"
    assert [(e["timestamp"], e["message"]) for e in info.events] == [
        ("2026-10-24T13:51:00+02:00", "En cours de livraison"),
        (
            "2026-10-24T06:03:00+02:00",
            "Le colis est arrivé à l'agence de livraison finale",
        ),
        ("2026-10-23T00:00:00+02:00", "Le colis a quitté les locaux de l'expéditeur"),
    ]
    assert caplog.text == ""


def test_french_tracking_id_label_is_the_fallback_to_the_state():
    page = track_page(
        tracking_id=ACTIVE_CODE, tracking_label="Numéro de suivi :", state=False
    )
    assert parse_track_page(page, FR_TODAY).tracking_id == ACTIVE_CODE


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Livré : hier", date(2026, 10, 23)),
        ("Livré aujourd'hui", date(2026, 10, 24)),
        # 2026-10-24 is a Saturday.
        ("Livré jeudi", date(2026, 10, 22)),
        ("Livré samedi", date(2026, 10, 17)),
        ("Livré le 14 Oct 2026", date(2026, 10, 14)),
        ("Livré", None),
        ("Livré aujourd’hui", date(2026, 10, 24)),
    ],
)
def test_french_relative_delivery_days(text, expected):
    (parsed,) = parse_order_tiles(
        orders_page(_fr_tile(DELIVERED_SHIPMENT, text, placed="10 octobre 2026")),
        FR_TODAY,
    )
    assert parsed.delivered_on == expected


def test_business_wording_is_confirmed(caplog):
    (parsed,) = parse_order_tiles(
        orders_page(
            _fr_tile(
                ACTIVE_SHIPMENT, "Livraison prévue lundi", placed="21 octobre 2026"
            )
        ),
        FR_TODAY,
    )
    assert parsed.status_text == "Livraison prévue lundi"
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# delivery window
# ---------------------------------------------------------------------------

# 2026-10-24 11:30 UTC is 13:30 in Paris; the window ends at 16:30.
READ_AT = datetime(2026, 10, 24, 11, 30, tzinfo=timezone.utc)
FR_STATE = {
    "timezone": "Europe/Paris",
    "promise": {
        "promiseMessage": "Arrive aujourd'hui dans la tranche horaire suivante : 14:00 - 16:30"
    },
    "timeUntilPredictedDeliveryWindowEnd": 10_812_000,
}


def test_delivery_window_from_the_promise_and_its_countdown():
    assert delivery_window(FR_STATE, READ_AT) == (
        "2026-10-24T14:00:00+02:00",
        "2026-10-24T16:30:00+02:00",
    )


@pytest.mark.parametrize(
    "message",
    [
        "Arriving today 2:00 PM - 4:30 PM",
        "Arriving today 2:00PM - 4:30PM",
        "Arriving today 2 pm - 4:30 pm",
        "Arriving today 2pm – 4:30pm",
    ],
)
def test_delivery_window_with_twelve_hour_times(message):
    state = {**FR_STATE, "promise": {"promiseMessage": message}}
    assert delivery_window(state, READ_AT) == (
        "2026-10-24T14:00:00+02:00",
        "2026-10-24T16:30:00+02:00",
    )


def test_delivery_window_written_in_hours():
    state = {**FR_STATE, "promise": {"promiseMessage": "Zustellung heute 14h - 16h30"}}
    assert delivery_window(state, READ_AT) == (
        "2026-10-24T14:00:00+02:00",
        "2026-10-24T16:30:00+02:00",
    )


@pytest.mark.parametrize("countdown", [None, True])
def test_a_window_without_its_countdown_is_reported_once(countdown, caplog):
    state = {
        **FR_STATE,
        "promise": {"promiseMessage": "Zustellung Freitag 17h - 19h"},
        "timeUntilPredictedDeliveryWindowEnd": countdown,
    }
    assert delivery_window(state, READ_AT) is None
    assert delivery_window(state, READ_AT) is None
    assert caplog.text.count('"Zustellung Freitag #h - #h" without the countdown') == 1


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # #37: amazon.de sends no countdown; the message names the day.
        (
            "Ankunft heute 7h – 13h",
            ("2026-10-24T07:00:00+02:00", "2026-10-24T13:00:00+02:00"),
        ),
        (
            "Zustellung heute 17h - 19h",
            ("2026-10-24T17:00:00+02:00", "2026-10-24T19:00:00+02:00"),
        ),
        (
            "Zustellung morgen 14h - 16h30",
            ("2026-10-25T14:00:00+01:00", "2026-10-25T16:30:00+01:00"),
        ),
        # #39: amazon.de in English, bare hours.
        (
            "Now arriving today 3 pm - 6 pm",
            ("2026-10-24T15:00:00+02:00", "2026-10-24T18:00:00+02:00"),
        ),
        (
            "Arriving tomorrow 11:00 AM - 1:15 PM",
            ("2026-10-25T11:00:00+01:00", "2026-10-25T13:15:00+01:00"),
        ),
        # amazon.nl, a rescheduled delivery: the next such weekday.
        (
            "Bezorging nu op vrijdag tussen 17h - 19h",
            ("2026-10-30T17:00:00+01:00", "2026-10-30T19:00:00+01:00"),
        ),
        (
            "Bezorging op zaterdag tussen 8h - 12h",
            ("2026-10-24T08:00:00+02:00", "2026-10-24T12:00:00+02:00"),
        ),
        (
            "Bezorging morgen tussen 17h - 19h",
            ("2026-10-25T17:00:00+01:00", "2026-10-25T19:00:00+01:00"),
        ),
        (
            "Bezorging vandaag tussen 8h - 12h",
            ("2026-10-24T08:00:00+02:00", "2026-10-24T12:00:00+02:00"),
        ),
    ],
)
def test_a_window_without_its_countdown_takes_the_day_it_names(message, expected):
    state = {
        "timezone": "Europe/Berlin",
        "promise": {"promiseMessage": message},
    }
    assert delivery_window(state, READ_AT) == expected


def test_a_reversed_window_naming_its_day_is_reported(caplog):
    state = {
        "timezone": "Europe/Berlin",
        "promise": {"promiseMessage": "Ankunft heute 13h - 7h"},
    }
    assert delivery_window(state, READ_AT) is None
    assert "without the countdown" in caplog.text


@pytest.mark.parametrize(
    "state",
    [
        {
            "timezone": "Europe/Paris",
            "promise": {"promiseMessage": "Livraison prévue lundi"},
            "timeUntilPredictedDeliveryWindowEnd": None,
        },
        {**FR_STATE, "timezone": None},
        {
            "timezone": "Europe/Paris",
            "promise": {"promiseMessage": "Livraison prévue lundi"},
        },
    ],
)
def test_delivery_window_needs_the_countdown_and_a_zone(state, caplog):
    assert delivery_window(state, READ_AT) is None
    assert caplog.text == ""


@pytest.mark.parametrize(
    "message",
    [
        "Arrive aujourd'hui",
        # Times that do not end where the countdown says.
        "Arrive aujourd'hui dans la tranche horaire suivante : 16:00 - 18:00",
        "Arrive aujourd'hui dans la tranche horaire suivante : 16:30 - 14:00",
    ],
)
def test_an_unreadable_window_is_left_out_and_reported_once(message, caplog):
    state = {**FR_STATE, "promise": {"promiseMessage": message}}
    assert delivery_window(state, READ_AT) is None
    assert delivery_window(state, READ_AT) is None
    assert caplog.text.count("delivery window") == 1


def test_record_carries_the_window_when_read_with_a_time():
    page = track_page(state=True, timezone="Europe/Paris")
    track = parse_track_page(page, FR_TODAY)
    track.state.update(FR_STATE)
    tiles = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None)), TODAY
    )
    assert build_record(COUNTRY, tiles, None, track)["delivery_window"] is None
    assert build_record(COUNTRY, tiles, None, track, read_at=READ_AT)[
        "delivery_window"
    ] == [
        "2026-10-24T14:00:00+02:00",
        "2026-10-24T16:30:00+02:00",
    ]


# ---------------------------------------------------------------------------
# courier not named on the page
# ---------------------------------------------------------------------------

COLISSIMO_ID = "6A00000000001"


def _unnamed_courier(tracking_id: str | None = COLISSIMO_ID):
    return parse_track_page(
        track_page(
            tracking_id=tracking_id,
            carrier_header="Informations sur la livraison",
            tracking_label="Numéro de suivi :",
            milestone="SHIPPED",
            short_status="IN_TRANSIT",
            timezone="Europe/Paris",
        ),
        FR_TODAY,
    )


def _active_tiles():
    return parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None)), TODAY
    )


def test_an_unnamed_courier_with_a_colissimo_id_is_guessed_as_la_poste(caplog):
    track = _unnamed_courier()
    assert track.carrier_code is None
    record = build_record("amazon.fr", _active_tiles(), None, track)
    build_record("amazon.fr", _active_tiles(), None, track)
    assert record["carrier_code"] == "LA_POSTE"
    assert record["carrier_guessed"] is True
    assert caplog.text.count("La Poste (Colissimo) was guessed") == 1


@pytest.mark.parametrize(
    "domain,tracking_id",
    [
        ("amazon.com.be", COLISSIMO_ID),
        ("amazon.fr", "FR0000000001"),
        ("amazon.fr", None),
    ],
)
def test_no_guess_off_amazon_fr_or_for_another_id_shape(domain, tracking_id):
    record = build_record(domain, _active_tiles(), None, _unnamed_courier(tracking_id))
    assert record["carrier_code"] is None
    assert record["carrier_guessed"] is False


def test_an_unread_courier_header_is_never_guessed_over(caplog):
    track = parse_track_page(
        track_page(
            tracking_id=COLISSIMO_ID, carrier_header="Livré par Unknown Couriers"
        ),
        FR_TODAY,
    )
    record = build_record("amazon.fr", _active_tiles(), None, track)
    assert record["carrier_code"] is None
    assert record["carrier_guessed"] is False
    assert "guessed" not in caplog.text


def test_a_named_courier_is_never_guessed():
    track = parse_track_page(
        track_page(tracking_id=COLISSIMO_ID, carrier="DHL_CONNECT"), FR_TODAY
    )
    record = build_record("amazon.fr", _active_tiles(), None, track)
    assert record["carrier_code"] == "DHL_CONNECT"
    assert record["carrier_guessed"] is False


# ---------------------------------------------------------------------------
# Amazon Business
# ---------------------------------------------------------------------------


def test_business_shell_gives_the_list_call():
    request = parse_business_shell(business_shell())
    assert request is not None
    assert request.token == BUSINESS_TOKEN
    form = request.form(0)
    assert form["refMarker"] == "ab_ppx_yo_mob_pagination_0"
    body = json.loads(form["deliveryHistoryRequestString"])
    assert body["marketplaceId"] == "fakeMarketId"
    assert body["businessId"] == "fakeBizId0"
    assert body["sessionId"] == "fakeSessn"
    assert body["pageNumber"] == 0
    # An empty string here silently returns no order at all.
    assert body["searchKeyword"] is None
    assert '"searchKeyword": null' in form["deliveryHistoryRequestString"]
    assert body["isDefaultFilterSelected"] is False
    assert body["filters"]["time"]["id"] == "yoPast3months"
    # Every order of the account: any buyer, any payer, any organisation.
    assert body["filters"]["users"]["id"] == "yoOrderedByAll"
    assert body["filters"]["paymentType"]["id"] == "yoAllPayments"
    assert body["filters"]["legalEntities"]["id"] == "yoAllOrganizations"
    assert body["originalRequestId"] == "0FAKEREQUESTID000000"
    assert "legalEntities" not in body
    # The page itself sends no customer id.
    assert "customerId" not in body


def test_business_shell_without_the_optional_values():
    page = business_shell().replace(
        '<input type="hidden" name="originalRequestId" value="0FAKEREQUESTID000000" id="originalRequestId">',
        "",
    )
    body = json.loads(
        parse_business_shell(page).form(0)["deliveryHistoryRequestString"]
    )
    assert "originalRequestId" not in body


def test_each_business_page_has_its_own_number_and_marker():
    form = parse_business_shell(business_shell()).form(3)
    assert json.loads(form["deliveryHistoryRequestString"])["pageNumber"] == 3
    assert form["refMarker"] == "ab_ppx_yo_mob_pagination_3"


def test_the_business_counters_are_read_whatever_their_case():
    # Amazon sends them camelCase; a browser's DOM shows them lower-cased.
    for fragment in (
        business_history(count=23, exception=True),
        business_history(count=23, exception=True).lower(),
    ):
        assert business_card_count(fragment) == 23
        assert business_history_failed(fragment)


@pytest.mark.parametrize(
    "page",
    [
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving today", None)),
        business_shell().replace('"sessionId":"fakeSessn"', '"sessionId":""'),
        business_shell().replace('"sessionId":"fakeSessn"}', '"sessionId":}'),
        business_shell().replace('id="ab-your-orders-anticsrf-token"', ""),
    ],
)
def test_not_a_usable_business_shell(page):
    assert parse_business_shell(page) is None


def test_business_cards_in_every_state(caplog):
    fragment = business_history(
        business_card(business_tracker_path("SHIPab00001"), "Livraison prévue lundi"),
        business_card(
            business_order_path("000-0000006-0000004"),
            "Remboursé",
            title="Example item 4",
            note="Votre retour est en transit. Votre remboursement a été émis.",
        ),
        business_card(
            business_order_path("000-0000006-0000005"),
            "Livré&nbsp;: hier",
            title="Example item 5",
        ),
        business_card(
            business_order_path("000-0000006-0000006"),
            "Livré jeudi",
            title="Example item 6",
        ),
        business_card(
            business_order_path("000-0000006-0000007"),
            "Livré le 14 Oct 2026",
            title="Example item 7",
        ),
        business_card(
            "/dp/B000TEST00/ref=ab_test",
            "Annulée",
            title="Example item 8",
            cancelled=True,
        ),
    )
    transit, yesterday, thursday, dated = parse_business_cards(fragment, FR_TODAY)

    assert transit.order_id == ORDER_ID
    assert transit.shipment_id == "SHIPab00001"
    assert transit.line_item_id == "item0001"
    assert transit.title == "Example item 1"
    assert transit.status_text == "Livraison prévue lundi"
    assert transit.placed_on is None
    assert transit.follow is True
    assert transit.delivered is False

    assert yesterday.status_text == "Livré : hier"
    assert yesterday.delivered_on == date(2026, 10, 23)
    assert yesterday.follow is False
    assert thursday.delivered_on == date(2026, 10, 22)
    assert dated.delivered_on == date(2026, 10, 14)
    assert caplog.text == ""


def test_a_delivered_business_card_keeps_its_key_whatever_its_place():
    card = business_card(business_order_path(), "Livré jeudi", title="Example item 6")
    other = business_card(business_order_path(), "Livré jeudi", title="Example item 9")
    (alone,) = parse_business_cards(business_history(card), FR_TODAY)
    _, moved = parse_business_cards(business_history(other, card), FR_TODAY)
    assert alone.key == moved.key
    assert alone.key != parse_business_cards(business_history(other), FR_TODAY)[0].key


def test_business_card_with_unconfirmed_wording_asks_once(caplog):
    fragment = business_history(
        business_card(business_tracker_path("SHIPab00009"), "Expédié aujourd'hui")
    )
    (parsed,) = parse_business_cards(fragment, FR_TODAY)
    assert parsed.status_text == "Expédié aujourd'hui"
    assert caplog.text.count("confirm it is right") == 1


def test_business_card_without_an_order_is_left_out_with_a_warning(caplog):
    card = business_card(business_order_path(""), "Livré jeudi", title="No order id")
    assert parse_business_cards(business_history(card, card), FR_TODAY) == []
    assert caplog.text.count("named no order") == 1


def test_a_delivered_line_without_a_readable_date_warns_once(caplog):
    page = orders_page(
        tile(DELIVERED_SHIPMENT, "Delivered to letterbox", None),
        tile("SHIPbare0001", "Delivered", None),
    )
    letterbox, bare = parse_order_tiles(page, TODAY)
    parse_order_tiles(page, TODAY)
    assert letterbox.delivered_on is None
    assert bare.delivered_on is None
    assert (
        caplog.text.count('"Delivered to letterbox" says the parcel was delivered') == 1
    )
    assert '"Delivered" says' not in caplog.text


def test_an_undated_delivered_business_card_warns(caplog):
    fragment = business_history(
        business_card(business_order_path(), "Livré la semaine dernière"),
        business_card(business_order_path(), "Livré", title="Example item 2"),
    )
    later, bare = parse_business_cards(fragment, FR_TODAY)
    assert later.delivered_on is None
    assert bare.delivered_on is None
    assert caplog.text.count("says the parcel was delivered") == 1


def test_business_list_count_and_error_flag():
    assert business_card_count(business_history()) == 0
    assert business_card_count(business_history(count=23)) == 23
    assert business_card_count("<div></div>") is None
    card = business_card(business_order_path(), "Livré")
    assert business_cards_found(business_history(card, card, count=3)) == 2
    assert business_history_failed(business_history(exception=True))
    assert not business_history_failed(business_history())


# ---------------------------------------------------------------------------
# whole-day window from an "Arriving ..." order line
# ---------------------------------------------------------------------------

ARRIVAL_TODAY = date(2026, 10, 25)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Arriving tomorrow", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Arriving today", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("Arriving Oct 27", (date(2026, 10, 27), date(2026, 10, 27))),
        ("Arriving Oct 27-29", (date(2026, 10, 27), date(2026, 10, 29))),
        ("Arriving Oct 30-Nov 2", (date(2026, 10, 30), date(2026, 11, 2))),
        ("Arriving Oct 27–29", (date(2026, 10, 27), date(2026, 10, 29))),
        # #40: day first, as amazon.de writes in English.
        ("Arriving 20 November", (date(2026, 11, 20), date(2026, 11, 20))),
        ("Arriving 3-5 November", (date(2026, 11, 3), date(2026, 11, 5))),
        ("Arriving 3 – 5 November", (date(2026, 11, 3), date(2026, 11, 5))),
        (
            "Arriving 20 November - 18 December",
            (date(2026, 11, 20), date(2026, 12, 18)),
        ),
        # #40: a weekday, the next such day; 2026-10-25 is a Sunday.
        ("Arriving Wednesday", (date(2026, 10, 28), date(2026, 10, 28))),
        ("Arriving Sunday", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("Arriving soon", None),
        ("Arriving Jan 3", (date(2027, 1, 3), date(2027, 1, 3))),
        ("Arriving today 11:00 AM - 1:15 PM", None),
        ("Arriving Foo 3", None),
        ("Arriving Feb 31", None),
        ("Morgen", None),
        ("Zustellung: Freitag", (date(2026, 10, 30), date(2026, 10, 30))),
        ("Zustellung: Sonntag", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("zustellung montag", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Zustellung: Freitag 17h - 19h", None),
        ("Zustellung: Foo", None),
        ("Ankunft heute", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("Ankunft morgen", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Zustellung heute", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("Zustellung morgen", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Zustellung heute 17h - 19h", None),
        ("Zustellung: 27. Oktober", (date(2026, 10, 27), date(2026, 10, 27))),
        (
            "Zustellung: 30. Oktober - 2. November",
            (date(2026, 10, 30), date(2026, 11, 2)),
        ),
        ("Zustellung: 3. Januar", (date(2027, 1, 3), date(2027, 1, 3))),
        ("Zustellung: 31. Februar", None),
        ("Zustellung: 3. Foo", None),
        # #48: amazon.es.
        ("Llega hoy", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("llega  HOY", (ARRIVAL_TODAY, ARRIVAL_TODAY)),
        ("Llega hoy 14:00 - 18:00", None),
        ("Llega mañana", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Llega MAÑANA", (date(2026, 10, 26), date(2026, 10, 26))),
        ("Llegará el 27 de noviembre", (date(2026, 11, 27), date(2026, 11, 27))),
        (
            "Llegará el jueves, 27 de noviembre",
            (date(2026, 11, 27), date(2026, 11, 27)),
        ),
        ("Llegará el 3 de enero", (date(2027, 1, 3), date(2027, 1, 3))),
        ("Llegará el 31 de febrero", None),
        ("Llegará el 3 de foo", None),
        (None, None),
    ],
)
def test_arrival_days(text, expected):
    assert arrival_days(text, ARRIVAL_TODAY) == expected


def test_an_arriving_line_gives_a_whole_day_window():
    zone = ZoneInfo("Europe/Amsterdam")
    read_at = datetime(2026, 10, 25, 19, 0, tzinfo=zone)
    tiles = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Arriving tomorrow", None)), TODAY
    )
    record = build_record("amazon.nl", tiles, None, None, read_at=read_at)
    assert record["delivery_window"] == [
        "2026-10-26T00:00:00+01:00",
        "2026-10-26T23:59:59+01:00",
    ]
    parcel = normalize_parcel(record)
    assert parcel["planned_from"] == "2026-10-26T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-10-26T23:59:59+01:00"


def _arrival_record(text, track=None):
    zone = ZoneInfo("Europe/Berlin")
    read_at = datetime(2026, 11, 19, 9, 0, tzinfo=zone)
    tiles = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, text, None)), read_at.date()
    )
    return normalize_parcel(
        build_record("amazon.de", tiles, None, track, read_at=read_at)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # #40: the order line is the only source without a tracking page.
        (
            "Arriving today 6 pm – 10 pm",
            ("2026-11-19T18:00:00+01:00", "2026-11-19T22:00:00+01:00"),
        ),
        (
            "Arriving tomorrow 11:00 AM - 1:15 PM",
            ("2026-11-20T11:00:00+01:00", "2026-11-20T13:15:00+01:00"),
        ),
        # Times that cannot be read still give the day.
        (
            "Arriving today 10 pm - 6 pm",
            ("2026-11-19T00:00:00+01:00", "2026-11-19T23:59:59+01:00"),
        ),
    ],
)
def test_a_timed_arriving_line_gives_its_window(text, expected, caplog):
    parcel = _arrival_record(text)
    assert (parcel["planned_from"], parcel["planned_to"]) == expected
    assert caplog.text == ""


def test_a_timed_arriving_line_defers_to_the_tracking_page_promise():
    # The page's own promise was read and dropped; the order line only gives
    # the day, never the times the page could not confirm.
    track = parse_track_page(
        track_page(milestone="SHIPPED", timezone="Europe/Berlin"), TODAY
    )
    track.state["promise"] = {"promiseMessage": "Arriving today 6 pm – 10 pm"}
    track.state["timeUntilPredictedDeliveryWindowEnd"] = 1_000
    parcel = _arrival_record("Arriving today 6 pm – 10 pm", track)
    assert parcel["planned_from"] == "2026-11-19T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-11-19T23:59:59+01:00"


def test_an_unread_arriving_line_warns_once(caplog):
    for _ in range(2):
        parcel = _arrival_record("Arriving soon")
        assert parcel["planned_from"] is None
    assert caplog.text.count('"Arriving soon", in wording we cannot read') == 1


def test_a_spanish_llega_hoy_line_keeps_its_window_after_a_tracking_event(caplog):
    # #48: the status comes from the timeline once the courier scans the
    # parcel; the order line still names the day.
    zone = ZoneInfo("Europe/Madrid")
    read_at = datetime(2026, 11, 19, 6, 0, tzinfo=zone)
    tiles = parse_order_tiles(
        orders_page(tile(ACTIVE_SHIPMENT, "Llega hoy", None)), read_at.date()
    )
    track = parse_track_page(
        track_page(milestone="SHIPPED", timezone="Europe/Madrid"), read_at.date()
    )
    parcel = normalize_parcel(
        build_record("amazon.es", tiles, None, track, read_at=read_at)
    )
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["planned_from"] == "2026-11-19T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-11-19T23:59:59+01:00"
    assert "cannot read" not in caplog.text


def test_an_unread_spanish_llega_line_warns_once(caplog):
    for _ in range(2):
        parcel = _arrival_record("Llega pronto")
        assert parcel["planned_from"] is None
    assert caplog.text.count('"Llega pronto", in wording we cannot read') == 1


def test_a_spanish_llega_manana_line_gives_tomorrow_as_a_whole_day(caplog):
    # #48: amazon.es, the day before delivery.
    parcel = _arrival_record("Llega mañana")
    assert parcel["planned_from"] == "2026-11-20T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-11-20T23:59:59+01:00"
    assert "cannot read" not in caplog.text


def test_a_german_zustellung_weekday_line_gives_a_whole_day_window():
    # amazon.de, a Saturday: "Zustellung: Freitag" is the coming Friday.
    zone = ZoneInfo("Europe/Berlin")
    read_at = datetime(2026, 11, 14, 19, 0, tzinfo=zone)
    tiles = parse_order_tiles(
        orders_page(
            tile(
                ACTIVE_SHIPMENT,
                "Zustellung: Freitag",
                None,
                placed="2. November 2026",
                placed_anchor="Bestellung aufgegeben",
            )
        ),
        read_at.date(),
    )
    record = build_record("amazon.de", tiles, None, None, read_at=read_at)
    parcel = normalize_parcel(record)
    assert parcel["planned_from"] == "2026-11-20T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-11-20T23:59:59+01:00"


def _untrackable(promise: str) -> object:
    info = parse_track_page(
        track_page(
            tracking_id=None, carrier=None, milestone=None, short_status="IN_TRANSIT"
        ),
        TODAY,
    )
    info.state["exceptionStateIdentifier"] = "UNTRACKABLE_STILL_EXPECTED_DELIVERED"
    info.state["promise"] = {"promiseMessage": promise}
    return info


def test_untrackable_shipment_keeps_its_promised_day():
    info = _untrackable("Wahrscheinlich 14. Oktober zugestellt")
    record = build_record(COUNTRY, _tiles("Unterwegs"), None, info)
    assert record["promised_on"] == "2026-10-14"


def test_promised_day_is_read_only_for_an_untrackable_shipment():
    info = parse_track_page(track_page(), TODAY)
    info.state["promise"] = {"promiseMessage": "Wahrscheinlich 14. Oktober zugestellt"}
    assert build_record(COUNTRY, _tiles(), None, info)["promised_on"] is None


def test_untrackable_promise_without_a_day_warns_once(caplog):
    info = _untrackable("Wahrscheinlich bald zugestellt")
    assert build_record(COUNTRY, _tiles("Unterwegs"), None, info)["promised_on"] is None
    build_record(COUNTRY, _tiles("Unterwegs"), None, info)
    assert caplog.text.count("Wahrscheinlich bald zugestellt") == 1


def test_amazon_com_drops_the_map_key_in_its_own_casing():
    # #32: amazon.com writes "hereMapsAPIKey".
    page = track_page().replace("hereMapsApiKey", "hereMapsAPIKey")
    info = parse_track_page(page, TODAY)
    assert FAKE_MAPS_KEY not in str(info.state)
    assert "customerId" in info.state

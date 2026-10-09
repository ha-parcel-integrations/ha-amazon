"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — ``parcels.py`` is free of I/O so the
carrier-specific mapping can be tested as plain functions.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    normalize_parcel,
    parse_iso,
    resolve_status,
    sort_parcels_by_ts,
    tracking_url,
)
from custom_components.amazon_orders.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)

from ..payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_record,
    delivered_record,
    event,
    in_transit_record,
    untracked_record,
)

# ---------------------------------------------------------------------------
# status resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Package arrived at an Amazon facility", ParcelStatus.IN_TRANSIT),
        ("Package departed an Amazon facility", ParcelStatus.IN_TRANSIT),
        ("Parcel arrived at a carrier facility", ParcelStatus.IN_TRANSIT),
        ("Package left the courier facility", ParcelStatus.IN_TRANSIT),
        ("Out for delivery", ParcelStatus.OUT_FOR_DELIVERY),
        ("Delivered to customer", ParcelStatus.DELIVERED),
        ("Delivered to letterbox", ParcelStatus.DELIVERED),
        ("  OUT FOR DELIVERY ", ParcelStatus.OUT_FOR_DELIVERY),
    ],
)
def test_event_messages_map_to_canonical_statuses(message, expected):
    assert map_event_status(message) == expected


def test_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("Teleported to the moon") is None


def test_unmapped_event_warns_only_once(caplog):
    map_event_status("Teleported to the moon")
    map_event_status("Teleported to the moon")
    assert caplog.text.count("Teleported to the moon") == 1
    assert "issues/new" in caplog.text


def test_delivered_milestone_wins_over_the_timeline():
    raw = delivered_record()
    raw["events"] = [event("2026-10-03T08:05:00+02:00", "Out for delivery")]
    assert resolve_status(raw) is ParcelStatus.DELIVERED


def test_unmapped_milestone_falls_back_to_the_newest_mappable_event(caplog):
    """The band-ordering trap: an unseen milestone name must not hide a
    perfectly good timeline, but is still reported once so the map can grow."""
    raw = active_record()
    raw["milestone"] = "SOMETHING_UNSEEN"
    raw["events"] = [
        event("2026-10-03T08:05:00+02:00", "Out for delivery"),
        event("2026-10-03T09:00:00+02:00", "A message we have never seen"),
    ]
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY
        assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY
    assert caplog.text.count("milestone=SOMETHING_UNSEEN") == 1


def test_delivered_short_status_resolves_without_milestone_or_timeline():
    raw = untracked_record()
    raw["order_status"] = "Something unseen"
    raw["page_state"] = {"shortStatus": "DELIVERED"}
    assert resolve_status(raw) is ParcelStatus.DELIVERED


def test_unmapped_short_status_falls_back_and_is_reported_once(caplog):
    raw = active_record()
    raw["milestone"] = None
    raw["page_state"] = {"shortStatus": "SOMETHING_UNSEEN"}
    raw["events"] = [event("2026-10-03T08:05:00+02:00", "Out for delivery")]
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY
        assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY
    assert caplog.text.count("short_status=SOMETHING_UNSEEN") == 1


def test_newest_mappable_event_decides_not_the_oldest():
    raw = in_transit_record()
    raw["milestone"] = None
    raw["events"] = [
        event("2026-10-03T02:55:00+02:00", "Parcel arrived at a carrier facility"),
        event("2026-10-03T08:05:00+02:00", "Out for delivery"),
    ]
    assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY


def test_order_text_resolves_a_shipment_without_a_tracking_page():
    assert resolve_status(untracked_record()) is ParcelStatus.REGISTERED
    raw = untracked_record()
    raw["order_status"] = "Delivered 3 October"
    assert resolve_status(raw) is ParcelStatus.DELIVERED


def test_nothing_mappable_is_unknown_with_one_warning(caplog):
    raw = untracked_record()
    raw["order_status"] = "Something unseen 7 October"
    raw["milestone"] = "SOMETHING_UNSEEN"
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is ParcelStatus.UNKNOWN
        assert resolve_status(raw) is ParcelStatus.UNKNOWN
    assert caplog.text.count("Unrecognised Amazon status") == 1
    assert "SOMETHING_UNSEEN" in caplog.text
    # Digits are stripped, so a moving date cannot make the warning repeat.
    assert "Something unseen #" in caplog.text
    assert "issues/new" in caplog.text


def test_a_line_with_no_status_text_and_no_tracking_is_unknown_not_registered(caplog):
    """The #1 failure mode: unreadable text must not become a parcel on its way."""
    raw = untracked_record()
    raw["order_status"] = None
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is ParcelStatus.UNKNOWN
    assert caplog.text.count("Unrecognised Amazon status") == 1


def test_unseen_wording_with_no_tracking_is_unknown_and_reported_masked(caplog):
    raw = untracked_record()
    raw["order_status"] = "Preparing 3 items"
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is ParcelStatus.UNKNOWN
        assert resolve_status(raw) is ParcelStatus.UNKNOWN
    assert caplog.text.count("Preparing # items") == 1
    assert "issues/new" in caplog.text


def test_only_recognised_not_yet_dispatched_text_is_registered(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_status(untracked_record()) is ParcelStatus.REGISTERED
    # Plausible wording: resolved, but reported once for confirmation.
    assert caplog.text.count("Not Yet Dispatched") == 1
    assert "confirm" in caplog.text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Shipped", ParcelStatus.IN_TRANSIT),
        ("Dispatched 3 October", ParcelStatus.IN_TRANSIT),
        ("Arriving tomorrow", ParcelStatus.IN_TRANSIT),
        ("Delivered 3 October", ParcelStatus.DELIVERED),
    ],
)
def test_confirmed_english_order_text_resolves_silently(text, expected, caplog):
    raw = untracked_record()
    raw["order_status"] = text
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is expected
    assert caplog.text == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Out for delivery", ParcelStatus.OUT_FOR_DELIVERY),
        ("Verwacht 3 oktober", ParcelStatus.IN_TRANSIT),
        ("Livré le 3 octobre", ParcelStatus.DELIVERED),
        ("Zugestellt am 3. Oktober", ParcelStatus.DELIVERED),
        ("Entregado el 3 de octubre", ParcelStatus.DELIVERED),
        ("Nog niet verzonden", ParcelStatus.REGISTERED),
    ],
)
def test_plausible_order_text_resolves_with_one_confirmation_warning(
    text, expected, caplog
):
    raw = untracked_record()
    raw["order_status"] = text
    with caplog.at_level(logging.WARNING):
        assert resolve_status(raw) is expected
        assert resolve_status(raw) is expected
    assert caplog.text.count("confirm it is right") == 1
    assert "#" in caplog.text or "#" not in text
    assert "issues/new" in caplog.text


def test_the_confirmation_warning_masks_digits(caplog):
    raw = untracked_record()
    raw["order_status"] = "Verwacht 3 oktober"
    with caplog.at_level(logging.WARNING):
        resolve_status(raw)
    assert "Verwacht # oktober" in caplog.text
    assert "Verwacht 3" not in caplog.text


def test_skip_wording_is_not_a_status():
    raw = untracked_record()
    raw["order_status"] = "Cancelled"
    assert resolve_status(raw) is ParcelStatus.UNKNOWN


def test_any_delivered_message_counts_as_delivery():
    raw = in_transit_record()
    raw["milestone"] = None
    raw["events"] = [event("2026-10-03T08:05:00+02:00", "Delivered to a neighbour")]
    assert resolve_status(raw) is ParcelStatus.DELIVERED


def test_events_without_a_message_are_skipped_in_resolution():
    raw = in_transit_record()
    raw["milestone"] = None
    raw["events"] = [
        {"timestamp": "2026-10-03T09:00:00+02:00"},
        event("2026-10-03T08:05:00+02:00", "Out for delivery"),
    ]
    assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY


def test_resolution_tolerates_malformed_events():
    raw = in_transit_record()
    raw["milestone"] = None
    raw["events"] = ["junk", {"timestamp": "bad", "message": "Out for delivery"}]
    assert resolve_status(raw) is ParcelStatus.OUT_FOR_DELIVERY


# ---------------------------------------------------------------------------
# timestamp helper
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_record()["events"])
    assert len(history) == 4
    assert history[0]["raw_status"] == "Package left the courier facility"
    assert history[0]["status"] == ParcelStatus.IN_TRANSIT
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    assert set(history[0]) == {"timestamp", "status", "raw_status"}


def test_build_history_caps_to_max_events():
    events = [
        event(f"2026-04-{day:02d}T10:00:00+00:00", "Out for delivery")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20
    assert len(build_history(events)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"message": "Out for delivery"}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("2026-04-24T10:00:00+00:00", "Out for delivery"),
            event("not-a-date", "Delivered to customer"),
        ]
    )
    assert [entry["raw_status"] for entry in history] == [
        "Out for delivery",
        "Delivered to customer",
    ]


def test_unmapped_history_message_keeps_null_status(caplog):
    history = build_history([event("2026-04-24T10:00:00+00:00", "Something new")])
    assert history[0]["status"] is None
    assert history[0]["raw_status"] == "Something new"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_record())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared capability comes true; every other field stays ``None``."""
    delivered = normalize_parcel(delivered_record())
    active = normalize_parcel(active_record())
    with_history = normalize_parcel(delivered_record(), include_history=True)

    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None
    for field, flag in (
        ("weight", "weight"),
        ("dimensions", "dimensions"),
        ("pickup_point", "pickup_point"),
    ):
        if flag not in CAPABILITIES:
            assert delivered[field] is None and active[field] is None
    # Pending: not confirmed by a real parcel yet, so it must still be null.
    assert "delivery_window" in PENDING_CAPABILITIES
    assert active["planned_from"] is None and active["planned_to"] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_record())
    assert parcel["carrier"] == "Dragonfly"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered to customer"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-10-03T10:36:00+02:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["url"].startswith("https://www.amazon.nl/-/en/gp/your-account/ship-track?")
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_record(), include_history=True)
    assert len(parcel["history"]) == 4
    assert parcel["history"][0]["status"] == ParcelStatus.IN_TRANSIT


def test_normalize_active_parcel():
    parcel = normalize_parcel(active_record())
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == "Out for delivery"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_untracked_shipment_keys_on_the_shipment():
    parcel = normalize_parcel(untracked_record())
    assert parcel["carrier"] == "Amazon"
    assert parcel["barcode"] == "SHIPnotrack1"
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert parcel["raw_status"] == "Not Yet Dispatched"
    assert parcel["url"].startswith("https://www.amazon.nl/-/en/your-orders/pop")
    assert parcel["raw"]["barcode_source"] == "shipment_key"


def test_shipment_key_includes_the_package_when_it_is_not_the_first():
    raw = untracked_record()
    raw["package_id"] = "2"
    assert normalize_parcel(raw)["barcode"] == "SHIPnotrack1-2"
    raw["shipment_id"] = None
    assert normalize_parcel(raw)["barcode"] is None


def test_delivered_without_events_uses_the_order_line_day():
    raw = untracked_record()
    raw["order_status"] = "Delivered 3 October"
    raw["delivered_on"] = "2026-10-03"
    parcel = normalize_parcel(raw)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-10-03T00:00:00+00:00"
    raw["delivered_on"] = None
    assert normalize_parcel(raw)["delivered_at"] is None


def test_delivered_at_is_the_newest_delivered_event():
    raw = delivered_record()
    raw["events"].append(event("2026-10-04T09:00:00+02:00", "Delivered to customer"))
    assert normalize_parcel(raw)["delivered_at"] == "2026-10-04T09:00:00+02:00"


def test_carrier_names(caplog):
    raw = active_record()
    raw["carrier_code"] = "DHL_CONNECT"
    assert normalize_parcel(raw)["carrier"] == "DHL"
    assert "DHL_CONNECT" not in caplog.text
    raw["carrier_code"] = "COLIS_PRIVE_BELU"
    assert normalize_parcel(raw)["carrier"] == "Colis Privé"
    raw["carrier_code"] = "Amazon"
    assert normalize_parcel(raw)["carrier"] == "Amazon"
    assert "carrier_code=" not in caplog.text
    raw["carrier_code"] = "SOME_NEW_CARRIER"
    assert normalize_parcel(raw)["carrier"] == "Some New Carrier"
    assert normalize_parcel(raw)["carrier"] == "Some New Carrier"
    assert caplog.text.count("SOME_NEW_CARRIER") == 1
    assert "issues/new" in caplog.text
    raw["carrier_code"] = None
    assert normalize_parcel(raw)["carrier"] == "Amazon"


def test_unread_carrier_header_is_reported_once(caplog):
    raw = active_record()
    raw["carrier_code"] = None
    raw["carrier_header"] = "Bezorgd door Testcarrier 12"
    assert normalize_parcel(raw)["carrier"] == "Amazon"
    assert normalize_parcel(raw)["carrier"] == "Amazon"
    assert caplog.text.count("Bezorgd door Testcarrier #") == 1
    assert "12" not in caplog.text
    assert "issues/new" in caplog.text


def test_tracked_shipment_without_any_carrier_is_reported_once(caplog):
    raw = active_record()
    raw["carrier_code"] = None
    raw["carrier_header"] = None
    tracking_id, raw["tracking_id"] = raw["tracking_id"], None
    normalize_parcel(raw)
    assert "named no delivery carrier" not in caplog.text

    raw["tracking_id"] = tracking_id
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("named no delivery carrier") == 1


def test_raw_status_falls_back_to_the_order_line_then_the_milestone():
    raw = delivered_record()
    raw["events"] = []
    assert normalize_parcel(raw)["raw_status"] == "Delivered 3 October"
    raw["order_status"] = None
    assert normalize_parcel(raw)["raw_status"] == "DELIVERED"


def test_normalize_keeps_the_full_raw_record():
    raw = active_record()
    assert normalize_parcel(raw)["raw"] is raw


def test_tracking_url_accepts_absolute_and_missing_paths():
    assert tracking_url({"track_path": "https://example.test/x"}) == "https://example.test/x"
    assert tracking_url({}) is None


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels

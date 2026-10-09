"""Tests for the order-line wording and month tables."""
import pytest

from custom_components.amazon_orders.account.vocabulary import (
    MONTHS,
    SKIP,
    STATUS_PREFIXES,
    classify_order_text,
)
from custom_components.amazon_orders.const import ParcelStatus


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Delivered 3 October", ParcelStatus.DELIVERED),
        ("Delivered Oct 3, 2026", ParcelStatus.DELIVERED),
        ("Arriving today 9:00 AM - 1:00 PM", ParcelStatus.IN_TRANSIT),
        ("Arriving Oct 5-Oct 7", ParcelStatus.IN_TRANSIT),
        ("Shipped", ParcelStatus.IN_TRANSIT),
        ("Dispatched", ParcelStatus.IN_TRANSIT),
        ("Cancelled", SKIP),
        ("Return complete", SKIP),
        ("Refund for this return", SKIP),
        ("Bezorgd op 8 oktober", ParcelStatus.DELIVERED),
        ("Wordt vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
        ("Wordt morgen bezorgd", ParcelStatus.IN_TRANSIT),
    ],
)
def test_confirmed_wording(text, kind):
    assert classify_order_text(text) == (kind, True)


def test_not_yet_dispatched_is_not_read_as_dispatched():
    assert classify_order_text("Not Yet Dispatched") == (
        ParcelStatus.REGISTERED,
        False,
    )


def test_unknown_and_empty_text_classify_as_nothing():
    assert classify_order_text("Something else") == (None, False)
    assert classify_order_text(None) == (None, False)


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
        ("Wordt bezorgd donderdag", ParcelStatus.IN_TRANSIT),
        ("Wordt donderdag bezorgd", ParcelStatus.IN_TRANSIT),
        ("Geannuleerd", SKIP),
        ("Pas encore expédié", ParcelStatus.REGISTERED),
        ("Annulé", SKIP),
        ("Noch nicht versandt", ParcelStatus.REGISTERED),
        ("Storniert", SKIP),
        ("Llega hoy", ParcelStatus.OUT_FOR_DELIVERY),
        ("Cancelado", SKIP),
        ("In consegna oggi", ParcelStatus.OUT_FOR_DELIVERY),
        ("Annullato", SKIP),
        ("Levereras idag", ParcelStatus.OUT_FOR_DELIVERY),
        ("Avbruten", SKIP),
        ("Dostawa dzisiaj", ParcelStatus.OUT_FOR_DELIVERY),
        ("Dostawa 5 października", ParcelStatus.IN_TRANSIT),
        ("Anulowano", SKIP),
    ],
)
def test_plausible_wording_is_flagged_unconfirmed(text, kind):
    assert classify_order_text(text) == (kind, False)


def test_longest_prefix_wins_and_every_prefix_is_listed():
    assert "not yet dispatched" in STATUS_PREFIXES
    assert STATUS_PREFIXES.index("not yet dispatched") < STATUS_PREFIXES.index(
        "shipped"
    )
    lengths = [len(prefix) for prefix in STATUS_PREFIXES]
    assert lengths == sorted(lengths, reverse=True)


@pytest.mark.parametrize(
    "name,number",
    [
        ("october", 10), ("oct", 10), ("sept", 9), ("märz", 3), ("okt", 10),
        ("dez", 12), ("janv", 1), ("févr", 2), ("juil", 7), ("août", 8),
        ("décembre", 12), ("septembre", 9), ("novembre", 11),
        ("octubre", 10), ("ottobre", 10), ("oktober", 10), ("października", 10),
        ("mrt", 3), ("maj", 5), ("stycznia", 1), ("września", 9),
    ],
)
def test_month_names(name, number):
    assert MONTHS[name] == number


def test_every_month_number_is_reachable():
    assert set(MONTHS.values()) == set(range(1, 13))

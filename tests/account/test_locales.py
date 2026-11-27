"""Tests for the order-line wording and month tables."""

import re

import pytest

from custom_components.amazon_orders.account.locales import (
    LOCALES,
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
        ("Vandaag bezorgd", ParcelStatus.DELIVERED),
        ("Wordt vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
        ("Wordt morgen bezorgd", ParcelStatus.IN_TRANSIT),
        ("Wordt bezorgd op donderdag", ParcelStatus.IN_TRANSIT),
        ("Livré : 7 octobre", ParcelStatus.DELIVERED),
        ("Livré aujourd'hui", ParcelStatus.DELIVERED),
        (
            "Arrive aujourd'hui dans la tranche horaire suivante : 14:00 - 16:30",
            ParcelStatus.OUT_FOR_DELIVERY,
        ),
        ("Arrive demain", ParcelStatus.IN_TRANSIT),
        ("Annulé(s)", SKIP),
        ("Llega hoy", ParcelStatus.IN_TRANSIT),
        ("Llega mañana", ParcelStatus.IN_TRANSIT),
        ("Llegará el 27 de noviembre", ParcelStatus.IN_TRANSIT),
        ("Entregado el 21 de noviembre", ParcelStatus.DELIVERED),
        ("Now arriving today 2 PM - 4 PM", ParcelStatus.IN_TRANSIT),
        ("Arriving now", ParcelStatus.OUT_FOR_DELIVERY),
        ("Arriving overnight 1 AM – 6 AM", ParcelStatus.IN_TRANSIT),
        ("Amazon Now Delivery", ParcelStatus.IN_TRANSIT),
        ("Delivered in 12 mins", ParcelStatus.DELIVERED),
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
        ("Wordt bezorgd donderdag", ParcelStatus.IN_TRANSIT),
        ("Wordt bezorgd op vrijdag", ParcelStatus.IN_TRANSIT),
        ("Wordt donderdag bezorgd", ParcelStatus.IN_TRANSIT),
        ("Geannuleerd", SKIP),
        ("Pas encore expédié", ParcelStatus.REGISTERED),
        ("Noch nicht versandt", ParcelStatus.REGISTERED),
        ("Storniert", SKIP),
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
        ("october", 10),
        ("oct", 10),
        ("sept", 9),
        ("märz", 3),
        ("okt", 10),
        ("dez", 12),
        ("dezember", 12),
        ("augustus", 8),
        ("janv", 1),
        ("févr", 2),
        ("juil", 7),
        ("août", 8),
        ("décembre", 12),
        ("septembre", 9),
        ("novembre", 11),
        ("octubre", 10),
        ("ottobre", 10),
        ("oktober", 10),
        ("października", 10),
        ("mrt", 3),
        ("maj", 5),
        ("stycznia", 1),
        ("września", 9),
    ],
)
def test_month_names(name, number):
    assert MONTHS[name] == number


def test_every_month_number_is_reachable():
    assert set(MONTHS.values()) == set(range(1, 13))


def test_a_curly_apostrophe_reads_like_a_straight_one():
    assert classify_order_text("Arrive aujourd’hui entre 14:00 et 16:30") == (
        ParcelStatus.OUT_FOR_DELIVERY,
        True,
    )


def _merged(field: str) -> dict[str, list[tuple[str, object]]]:
    seen: dict[str, list[tuple[str, object]]] = {}
    for code, locale in LOCALES.items():
        for key, value in getattr(locale, field).items():
            seen.setdefault(key, []).append((code, value))
    return seen


@pytest.mark.parametrize(
    "field",
    [
        "status_confirmed",
        "status_plausible",
        "months_confirmed",
        "months_plausible",
        "events",
    ],
)
def test_no_two_languages_read_a_word_differently(field):
    for word, readings in _merged(field).items():
        assert len({value for _, value in readings}) == 1, (word, readings)


def test_a_status_is_either_confirmed_or_plausible():
    confirmed = _merged("status_confirmed")
    assert not confirmed.keys() & _merged("status_plausible").keys()


@pytest.mark.parametrize("code", list(LOCALES))
def test_a_language_file_is_well_formed(code):
    locale = LOCALES[code]
    assert len(locale.past_weekdays) in (0, 7)
    assert len(locale.upcoming_weekdays) in (0, 7)
    for table in (
        locale.status_confirmed,
        locale.status_plausible,
        locale.months_confirmed,
        locale.months_plausible,
        locale.events,
    ):
        assert all(key == key.lower() for key in table)
    assert all(lead == lead.lower() for lead in locale.arrival_leads)
    assert all(1 <= n <= 12 for n in locale.months_plausible.values())
    assert all(1 <= n <= 12 for n in locale.months_confirmed.values())
    groups = {"rel", "weekday", "d1", "m1", "d2", "m2"}
    for pattern in locale.arrival + locale.window_day:
        assert set(re.compile(pattern).groupindex) <= groups
    for pattern in locale.window_day:
        assert {"rel", "weekday"} & set(re.compile(pattern).groupindex)

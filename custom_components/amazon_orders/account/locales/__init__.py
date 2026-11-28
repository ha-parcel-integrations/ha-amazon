"""Amazon's wording, one file per language, merged into the tables the parser reads.

To add a language or a phrase, edit (or copy) one language file; nothing here
needs to change. Every language is always tried, because the language follows
the account's setting, not the storefront.
"""

from __future__ import annotations

import re

from ...const import ParcelStatus
from . import de, en, es, fr, it, nl, pl, sv
from .base import SKIP, Kind, Locale

__all__ = ["LOCALES", "SKIP", "Kind", "Locale", "classify_order_text"]

LOCALES: dict[str, Locale] = {
    "en": en.LOCALE,
    "nl": nl.LOCALE,
    "de": de.LOCALE,
    "fr": fr.LOCALE,
    "es": es.LOCALE,
    "it": it.LOCALE,
    "sv": sv.LOCALE,
    "pl": pl.LOCALE,
}
_ALL = tuple(LOCALES.values())


def _phrases(phrases: list[str]) -> str:
    """Return an alternation of literal phrases, longest first."""
    ordered = sorted(set(phrases), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s") for p in ordered) + ")"


# Longest prefix first, so "not yet dispatched" is never read as a shorter one.
_TEXT: tuple[tuple[str, Kind, bool], ...] = tuple(
    sorted(
        [(p, k, True) for loc in _ALL for p, k in loc.status_confirmed.items()]
        + [(p, k, False) for loc in _ALL for p, k in loc.status_plausible.items()],
        key=lambda entry: len(entry[0]),
        reverse=True,
    )
)
STATUS_PREFIXES: tuple[str, ...] = tuple(prefix for prefix, _, _ in _TEXT)


def classify_order_text(text: str | None) -> tuple[Kind | None, bool]:
    """Return what an order-line status means and whether that is confirmed."""
    lowered = (text or "").strip().lower().replace("’", "'")
    for prefix, kind, confirmed in _TEXT:
        if lowered.startswith(prefix):
            return kind, confirmed
    return None, False


MONTHS: dict[str, int] = {
    **{name: n for loc in _ALL for name, n in loc.months_plausible.items()},
    **{name: n for loc in _ALL for name, n in loc.months_confirmed.items()},
}
TODAY_WORDS = frozenset(w for loc in _ALL for w in loc.today)
YESTERDAY_WORDS = frozenset(w for loc in _ALL for w in loc.yesterday)
TOMORROW_WORDS = frozenset(w for loc in _ALL for w in loc.tomorrow)
PAST_WEEKDAYS: dict[str, int] = {
    name: number for loc in _ALL for number, name in enumerate(loc.past_weekdays)
}
UPCOMING_WEEKDAYS: dict[str, int] = {
    name: number for loc in _ALL for number, name in enumerate(loc.upcoming_weekdays)
}
UNNAMED_CARRIER_HEADERS = frozenset(
    h for loc in _ALL for h in loc.unnamed_carrier_headers
)

ORDER_PLACED = _phrases([p for loc in _ALL for p in loc.order_placed])
DELIVERY_BY = _phrases([p for loc in _ALL for p in loc.delivery_by])
TRACKING_ID_LABEL = _phrases([p for loc in _ALL for p in loc.tracking_id_labels])
STOPS_AWAY = _phrases([p for loc in _ALL for p in loc.stops_away])
NEXT_STOP = _phrases([p for loc in _ALL for p in loc.next_stop])

EVENTS: dict[str, ParcelStatus] = {
    message: status for loc in _ALL for message, status in loc.events.items()
}
DELIVERED_EVENTS: tuple[str, ...] = tuple(
    prefix for loc in _ALL for prefix in loc.delivered_events
)

ARRIVAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for loc in _ALL for pattern in loc.arrival
)
ARRIVAL_LEADS: tuple[str, ...] = tuple(
    lead for loc in _ALL for lead in loc.arrival_leads
)
WINDOW_DAY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for loc in _ALL for pattern in loc.window_day
)
DELIVERED_TIME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for loc in _ALL for pattern in loc.delivered_time
)
UNDATED_DELIVERED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for loc in _ALL
    for pattern in loc.undated_delivered
)
OVERNIGHT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for loc in _ALL for pattern in loc.overnight
)

"""Order-line status wording and month names, by confidence.

Confirmed entries come from text seen in the code, tests or issues of other
order-history readers (or from our own captures). Plausible entries are
written from how Amazon is expected to phrase things and have not been seen in
the wild; resolving a status through one is reported once so it can be
confirmed or corrected.
"""
from __future__ import annotations

from typing import Final

from ..const import ParcelStatus

# A line that must never count as incoming (cancelled, returned, refunded).
SKIP: Final = "skip"

Kind = ParcelStatus | str

_CONFIRMED_TEXT: tuple[tuple[str, Kind], ...] = (
    ("delivered", ParcelStatus.DELIVERED),
    ("arriving", ParcelStatus.IN_TRANSIT),
    ("shipped", ParcelStatus.IN_TRANSIT),
    ("dispatched", ParcelStatus.IN_TRANSIT),
    ("cancelled", SKIP),
    ("return complete", SKIP),
    ("refund", SKIP),
    # nl, seen on amazon.com.be order lines
    ("bezorgd", ParcelStatus.DELIVERED),
    ("wordt vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
    ("wordt morgen bezorgd", ParcelStatus.IN_TRANSIT),
)

_PLAUSIBLE_TEXT: tuple[tuple[str, Kind], ...] = (
    # English wording not seen on a real order line.
    ("out for delivery", ParcelStatus.OUT_FOR_DELIVERY),
    ("not yet dispatched", ParcelStatus.REGISTERED),
    # nl
    ("verwacht", ParcelStatus.IN_TRANSIT),
    ("aankomst", ParcelStatus.IN_TRANSIT),
    ("vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
    ("wordt bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt maandag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt dinsdag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt woensdag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt donderdag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt vrijdag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt zaterdag bezorgd", ParcelStatus.IN_TRANSIT),
    ("wordt zondag bezorgd", ParcelStatus.IN_TRANSIT),
    ("nog niet verzonden", ParcelStatus.REGISTERED),
    ("verzonden", ParcelStatus.IN_TRANSIT),
    ("geannuleerd", SKIP),
    ("retour", SKIP),
    ("terugbetaald", SKIP),
    # fr
    ("livré", ParcelStatus.DELIVERED),
    ("arrivée prévue", ParcelStatus.IN_TRANSIT),
    ("livraison prévue", ParcelStatus.IN_TRANSIT),
    ("pas encore expédié", ParcelStatus.REGISTERED),
    ("expédié", ParcelStatus.IN_TRANSIT),
    ("en cours de livraison", ParcelStatus.OUT_FOR_DELIVERY),
    ("livraison aujourd'hui", ParcelStatus.OUT_FOR_DELIVERY),
    ("annulé", SKIP),
    ("retourné", SKIP),
    ("remboursé", SKIP),
    # de
    ("zugestellt", ParcelStatus.DELIVERED),
    ("geliefert", ParcelStatus.DELIVERED),
    ("ankunft", ParcelStatus.IN_TRANSIT),
    ("zustellung am", ParcelStatus.IN_TRANSIT),
    ("noch nicht versandt", ParcelStatus.REGISTERED),
    ("versandt", ParcelStatus.IN_TRANSIT),
    ("zustellung heute", ParcelStatus.OUT_FOR_DELIVERY),
    ("wird heute zugestellt", ParcelStatus.OUT_FOR_DELIVERY),
    ("storniert", SKIP),
    ("zurückgesendet", SKIP),
    ("erstattet", SKIP),
    # es
    ("entregado", ParcelStatus.DELIVERED),
    ("llegará", ParcelStatus.IN_TRANSIT),
    ("aún no enviado", ParcelStatus.REGISTERED),
    ("enviado", ParcelStatus.IN_TRANSIT),
    ("en reparto", ParcelStatus.OUT_FOR_DELIVERY),
    ("llega hoy", ParcelStatus.OUT_FOR_DELIVERY),
    ("cancelado", SKIP),
    ("devuelto", SKIP),
    ("reembolsado", SKIP),
    # it
    ("consegnato", ParcelStatus.DELIVERED),
    ("arriverà", ParcelStatus.IN_TRANSIT),
    ("consegna prevista", ParcelStatus.IN_TRANSIT),
    ("non ancora spedito", ParcelStatus.REGISTERED),
    ("spedito", ParcelStatus.IN_TRANSIT),
    ("in consegna", ParcelStatus.OUT_FOR_DELIVERY),
    ("annullato", SKIP),
    ("reso", SKIP),
    ("rimborsato", SKIP),
    # sv
    ("levererad", ParcelStatus.DELIVERED),
    ("beräknad leverans", ParcelStatus.IN_TRANSIT),
    ("anländer", ParcelStatus.IN_TRANSIT),
    ("ännu inte skickad", ParcelStatus.REGISTERED),
    ("skickad", ParcelStatus.IN_TRANSIT),
    ("levereras idag", ParcelStatus.OUT_FOR_DELIVERY),
    ("avbruten", SKIP),
    # pl
    ("dostarczono", ParcelStatus.DELIVERED),
    ("dostawa dzisiaj", ParcelStatus.OUT_FOR_DELIVERY),
    ("dostawa", ParcelStatus.IN_TRANSIT),
    ("przyjdzie", ParcelStatus.IN_TRANSIT),
    ("jeszcze nie wysłano", ParcelStatus.REGISTERED),
    ("wysłano", ParcelStatus.IN_TRANSIT),
    ("anulowano", SKIP),
)

# Longest prefix first, so "not yet dispatched" is never read as a shorter one.
_TEXT: tuple[tuple[str, Kind, bool], ...] = tuple(
    sorted(
        [(p, k, True) for p, k in _CONFIRMED_TEXT]
        + [(p, k, False) for p, k in _PLAUSIBLE_TEXT],
        key=lambda entry: len(entry[0]),
        reverse=True,
    )
)

STATUS_PREFIXES: tuple[str, ...] = tuple(prefix for prefix, _, _ in _TEXT)


def classify_order_text(text: str | None) -> tuple[Kind | None, bool]:
    """Return what an order-line status means and whether that is confirmed."""
    lowered = (text or "").strip().lower()
    for prefix, kind, confirmed in _TEXT:
        if lowered.startswith(prefix):
            return kind, confirmed
    return None, False


def _months(table: str) -> dict[str, int]:
    return {
        name: number
        for number, names in enumerate(table.split("|"), start=1)
        for name in names.split()
    }


# English, German and French, including the short forms the readers list, and
# the Dutch months seen on a real order line.
_CONFIRMED_MONTHS = _months(
    "january jan januar janvier janv|february feb februar février févr fév|"
    "march mar märz mars|april apr avril avr|may mai|june jun juni juin|"
    "july jul juli juillet juil|august aug août|"
    "september septembre sep sept|october oct okt octobre oktober|november novembre nov|"
    "december dec dez décembre déc"
)
# Italian, Spanish, Dutch, Swedish and Polish names: no source shows them.
_PLAUSIBLE_MONTHS = _months(
    "gennaio enero januari januari stycznia|"
    "febbraio febrero februari lutego|marzo marzo maart mrt marca|"
    "aprile abril kwietnia|maggio mayo mei maj maja|giugno junio czerwca|"
    "luglio julio juli lipca|agosto agosto augusti sierpnia|"
    "settembre septiembre września|ottobre octubre października|"
    "novembre noviembre listopada|dicembre diciembre december grudnia"
)

MONTHS: dict[str, int] = {**_PLAUSIBLE_MONTHS, **_CONFIRMED_MONTHS}

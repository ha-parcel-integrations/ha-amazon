"""German: amazon.de, amazon.at and amazon.ch."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_confirmed={
        "zugestellt": ParcelStatus.DELIVERED,
        "heute zugestellt": ParcelStatus.DELIVERED,
        "ankunft": ParcelStatus.IN_TRANSIT,
        "zustellung": ParcelStatus.IN_TRANSIT,
        "zustellung heute": ParcelStatus.OUT_FOR_DELIVERY,
    },
    status_plausible={
        "geliefert": ParcelStatus.DELIVERED,
        "zustellung am": ParcelStatus.IN_TRANSIT,
        "noch nicht versandt": ParcelStatus.REGISTERED,
        "versandt": ParcelStatus.IN_TRANSIT,
        "wird heute zugestellt": ParcelStatus.OUT_FOR_DELIVERY,
        "storniert": SKIP,
        "zurückgesendet": SKIP,
        "erstattet": SKIP,
    },
    months_confirmed={
        "januar": 1,
        "februar": 2,
        "märz": 3,
        "april": 4,
        "mai": 5,
        "juni": 6,
        "juli": 7,
        "august": 8,
        "september": 9,
        "oktober": 10,
        "okt": 10,
        "november": 11,
        "dezember": 12,
        "dez": 12,
    },
    today=("heute",),
    yesterday=("gestern",),
    tomorrow=("morgen",),
    upcoming_weekdays=(
        "montag",
        "dienstag",
        "mittwoch",
        "donnerstag",
        "freitag",
        "samstag",
        "sonntag",
    ),
    order_placed=("Bestellung aufgegeben am", "Bestellung aufgegeben"),
    delivery_by=("Versendet mit", "Versand durch", "Zustellung durch"),
    stops_away=("Stopp entfernt", "Stopps entfernt"),
    events={
        "das paket ist an einem amazon-standort eingetroffen": ParcelStatus.IN_TRANSIT,
        "das paket hat einen amazon-standort verlassen": ParcelStatus.IN_TRANSIT,
        "paket ist in der letzten lieferstation angekommen": ParcelStatus.IN_TRANSIT,
        "paket hat die einrichtung des versandunternehmers verlassen": (
            ParcelStatus.IN_TRANSIT
        ),
        "sendung wird zugestellt": ParcelStatus.OUT_FOR_DELIVERY,
        "im briefkasten hinterlegt": ParcelStatus.DELIVERED,
        "lieferung in der nähe von haustür oder veranda abgelegt": (
            ParcelStatus.DELIVERED
        ),
        "die sendung wurde vom empfänger abgeholt": ParcelStatus.DELIVERED,
    },
    delivered_events=("zugestellt",),
    arrival=(
        # "Ankunft morgen", "Zustellung heute".
        r"(?:ankunft|zustellung)(?::\s*|\s+)(?P<rel>heute|morgen)",
        # "Zustellung: 20. Oktober", "Zustellung: 20. Oktober - 21. Oktober".
        r"(?:ankunft|zustellung)(?::\s*|\s+)(?P<d1>\d{1,2})\.\s*(?P<m1>[^\W\d_]+)"
        r"(?:\s*[-–—]\s*(?P<d2>\d{1,2})\.\s*(?P<m2>[^\W\d_]+))?",
        # "Zustellung: Freitag" (#27): the next such day, today included.
        r"zustellung(?::\s*|\s+)(?P<weekday>[^\W\d_]+)",
    ),
    window_day=(
        # "Ankunft heute 7h – 13h" (#37): amazon.de sends no countdown.
        r"(?:ankunft|zustellung):?\s+(?P<rel>heute|morgen)\b",
    ),
)

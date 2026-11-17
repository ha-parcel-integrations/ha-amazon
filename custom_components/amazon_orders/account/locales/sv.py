"""Swedish: amazon.se. Nothing seen on a real page yet."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_plausible={
        "levererad": ParcelStatus.DELIVERED,
        "beräknad leverans": ParcelStatus.IN_TRANSIT,
        "anländer": ParcelStatus.IN_TRANSIT,
        "ännu inte skickad": ParcelStatus.REGISTERED,
        "skickad": ParcelStatus.IN_TRANSIT,
        "levereras idag": ParcelStatus.OUT_FOR_DELIVERY,
        "avbruten": SKIP,
    },
    months_plausible={
        "januari": 1,
        "februari": 2,
        "mars": 3,
        "april": 4,
        "maj": 5,
        "juni": 6,
        "juli": 7,
        "augusti": 8,
        "september": 9,
        "oktober": 10,
        "november": 11,
        "december": 12,
    },
    today=("idag",),
    yesterday=("igår",),
)

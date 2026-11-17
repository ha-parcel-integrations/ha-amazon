"""Italian: amazon.it. Nothing seen on a real page yet."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_plausible={
        "consegnato": ParcelStatus.DELIVERED,
        "arriverà": ParcelStatus.IN_TRANSIT,
        "consegna prevista": ParcelStatus.IN_TRANSIT,
        "non ancora spedito": ParcelStatus.REGISTERED,
        "spedito": ParcelStatus.IN_TRANSIT,
        "in consegna": ParcelStatus.OUT_FOR_DELIVERY,
        "annullato": SKIP,
        "reso": SKIP,
        "rimborsato": SKIP,
    },
    months_plausible={
        "gennaio": 1,
        "febbraio": 2,
        "marzo": 3,
        "aprile": 4,
        "maggio": 5,
        "giugno": 6,
        "luglio": 7,
        "agosto": 8,
        "settembre": 9,
        "ottobre": 10,
        "novembre": 11,
        "dicembre": 12,
    },
    today=("oggi",),
    yesterday=("ieri",),
)

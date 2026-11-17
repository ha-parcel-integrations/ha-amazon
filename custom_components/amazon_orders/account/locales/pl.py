"""Polish: amazon.pl. Nothing seen on a real page yet."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_plausible={
        "dostarczono": ParcelStatus.DELIVERED,
        "dostawa dzisiaj": ParcelStatus.OUT_FOR_DELIVERY,
        "dostawa": ParcelStatus.IN_TRANSIT,
        "przyjdzie": ParcelStatus.IN_TRANSIT,
        "jeszcze nie wysłano": ParcelStatus.REGISTERED,
        "wysłano": ParcelStatus.IN_TRANSIT,
        "anulowano": SKIP,
    },
    months_plausible={
        "stycznia": 1,
        "lutego": 2,
        "marca": 3,
        "kwietnia": 4,
        "maja": 5,
        "czerwca": 6,
        "lipca": 7,
        "sierpnia": 8,
        "września": 9,
        "października": 10,
        "listopada": 11,
        "grudnia": 12,
    },
    today=("dzisiaj",),
    yesterday=("wczoraj",),
)

"""Spanish: amazon.es. Nothing seen on a real page yet."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_plausible={
        "entregado": ParcelStatus.DELIVERED,
        "llegará": ParcelStatus.IN_TRANSIT,
        "aún no enviado": ParcelStatus.REGISTERED,
        "enviado": ParcelStatus.IN_TRANSIT,
        "en reparto": ParcelStatus.OUT_FOR_DELIVERY,
        "llega hoy": ParcelStatus.OUT_FOR_DELIVERY,
        "cancelado": SKIP,
        "devuelto": SKIP,
        "reembolsado": SKIP,
    },
    months_plausible={
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    },
    today=("hoy",),
    yesterday=("ayer",),
)

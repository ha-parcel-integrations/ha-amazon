"""Spanish: amazon.es."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    # "Llega hoy" (#48) shows before the parcel leaves the depot, as
    # "Arriving today" does.
    status_confirmed={
        "llega": ParcelStatus.IN_TRANSIT,
        "llegará": ParcelStatus.IN_TRANSIT,
        "entregado": ParcelStatus.DELIVERED,
    },
    status_plausible={
        "aún no enviado": ParcelStatus.REGISTERED,
        "enviado": ParcelStatus.IN_TRANSIT,
        "en reparto": ParcelStatus.OUT_FOR_DELIVERY,
        "cancelado": SKIP,
        "devuelto": SKIP,
        "reembolsado": SKIP,
    },
    months_confirmed={
        "noviembre": 11,
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
        "diciembre": 12,
    },
    today=("hoy",),
    yesterday=("ayer",),
    tomorrow=("mañana",),
    delivery_by=("Enviado con",),
    arrival=(
        r"llega\s+(?P<rel>hoy|mañana)",
        # "Llegará el 27 de noviembre", a weekday ahead of the day allowed.
        r"llegará\s+el\s+(?:[^\W\d_]+,?\s+)?(?P<d1>\d{1,2})\s+de\s+(?P<m1>[^\W\d_]+)",
    ),
    arrival_leads=("llega",),
)

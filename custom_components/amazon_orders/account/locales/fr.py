"""French: amazon.fr and amazon.com.be."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_confirmed={
        "livré": ParcelStatus.DELIVERED,
        "arrive aujourd'hui": ParcelStatus.OUT_FOR_DELIVERY,
        "arrive": ParcelStatus.IN_TRANSIT,
        "annulé": SKIP,
        "livraison prévue": ParcelStatus.IN_TRANSIT,
        "remboursé": SKIP,
    },
    status_plausible={
        "arrivée prévue": ParcelStatus.IN_TRANSIT,
        "pas encore expédié": ParcelStatus.REGISTERED,
        "expédié": ParcelStatus.IN_TRANSIT,
        "en cours de livraison": ParcelStatus.OUT_FOR_DELIVERY,
        "livraison aujourd'hui": ParcelStatus.OUT_FOR_DELIVERY,
        "retourné": SKIP,
    },
    months_confirmed={
        "janvier": 1,
        "janv": 1,
        "février": 2,
        "févr": 2,
        "fév": 2,
        "mars": 3,
        "avril": 4,
        "avr": 4,
        "mai": 5,
        "juin": 6,
        "juillet": 7,
        "juil": 7,
        "août": 8,
        "septembre": 9,
        "sept": 9,
        "octobre": 10,
        "novembre": 11,
        "décembre": 12,
        "déc": 12,
    },
    today=("aujourd'hui",),
    yesterday=("hier",),
    # "Livré jeudi", within the past week.
    past_weekdays=(
        "lundi",
        "mardi",
        "mercredi",
        "jeudi",
        "vendredi",
        "samedi",
        "dimanche",
    ),
    order_placed=("Commande effectuée le",),
    delivery_by=("Livraison par", "Expédié avec"),
    tracking_id_labels=("Numéro de suivi",),
    unnamed_carrier_headers=("informations sur la livraison",),
    events={
        "le colis a quitté les locaux de l'expéditeur": ParcelStatus.IN_TRANSIT,
        "le colis est arrivé à l'agence de livraison finale": ParcelStatus.IN_TRANSIT,
        "en cours de livraison": ParcelStatus.OUT_FOR_DELIVERY,
        "livré": ParcelStatus.DELIVERED,
    },
    delivered_events=("livré",),
)

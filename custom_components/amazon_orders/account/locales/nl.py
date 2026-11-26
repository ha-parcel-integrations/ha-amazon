"""Dutch: amazon.nl and amazon.com.be."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_confirmed={
        "bezorgd": ParcelStatus.DELIVERED,
        "vandaag bezorgd": ParcelStatus.DELIVERED,
        "wordt vandaag bezorgd": ParcelStatus.OUT_FOR_DELIVERY,
        "wordt morgen bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op donderdag": ParcelStatus.IN_TRANSIT,
    },
    status_plausible={
        "verwacht": ParcelStatus.IN_TRANSIT,
        "aankomst": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op maandag": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op dinsdag": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op woensdag": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op vrijdag": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op zaterdag": ParcelStatus.IN_TRANSIT,
        "wordt bezorgd op zondag": ParcelStatus.IN_TRANSIT,
        "wordt maandag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt dinsdag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt woensdag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt donderdag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt vrijdag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt zaterdag bezorgd": ParcelStatus.IN_TRANSIT,
        "wordt zondag bezorgd": ParcelStatus.IN_TRANSIT,
        "nog niet verzonden": ParcelStatus.REGISTERED,
        "verzonden": ParcelStatus.IN_TRANSIT,
        "geannuleerd": SKIP,
        "retour": SKIP,
        "terugbetaald": SKIP,
    },
    months_confirmed={"oktober": 10, "november": 11, "december": 12},
    months_plausible={
        "januari": 1,
        "februari": 2,
        "maart": 3,
        "mrt": 3,
        "april": 4,
        "mei": 5,
        "juni": 6,
        "juli": 7,
        "augustus": 8,
        "september": 9,
    },
    today=("vandaag",),
    tomorrow=("morgen",),
    yesterday=("gisteren",),
    upcoming_weekdays=(
        "maandag",
        "dinsdag",
        "woensdag",
        "donderdag",
        "vrijdag",
        "zaterdag",
        "zondag",
    ),
    delivery_by=("Bezorging door", "Levering door"),
    events={
        "je pakket is aangekomen bij een amazon-faciliteit": ParcelStatus.IN_TRANSIT,
        "je pakket heeft een amazon-faciliteit verlaten": ParcelStatus.IN_TRANSIT,
        "het pakket heeft de verzendingsfaciliteit verlaten": ParcelStatus.IN_TRANSIT,
        "pakket aangekomen bij het laatste bezorgstation": ParcelStatus.IN_TRANSIT,
        "pakket aangekomen bij transportdienst": ParcelStatus.IN_TRANSIT,
        "bezorgafspraak gepland": ParcelStatus.IN_TRANSIT,
        "klant heeft de levering van de zending opnieuw gepland met de vervoerder."
        " pakket wordt nu geleverd op de afgesproken datum": ParcelStatus.IN_TRANSIT,
        "uit voor levering": ParcelStatus.OUT_FOR_DELIVERY,
        "bezorgd in brievenbus": ParcelStatus.DELIVERED,
    },
    delivered_events=("bezorgd",),
    window_day=(
        # "Bezorging nu op vrijdag tussen 17h - 19h": a rescheduled delivery,
        # sent without a countdown.
        r"bezorging\s+(?:nu\s+)?op\s+(?P<weekday>[^\W\d_]+)\b",
        # "Bezorging morgen tussen 17h - 19h": amazon.nl sends no countdown.
        r"bezorging\s+(?P<rel>vandaag|morgen)\b",
    ),
)

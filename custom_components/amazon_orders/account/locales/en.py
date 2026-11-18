"""English: amazon.com, amazon.co.uk, amazon.ca and any account set to English."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_confirmed={
        "delivered": ParcelStatus.DELIVERED,
        "arriving": ParcelStatus.IN_TRANSIT,
        "shipped": ParcelStatus.IN_TRANSIT,
        "dispatched": ParcelStatus.IN_TRANSIT,
        "cancelled": SKIP,
        "return complete": SKIP,
        "refund": SKIP,
    },
    status_plausible={
        "out for delivery": ParcelStatus.OUT_FOR_DELIVERY,
        "not yet dispatched": ParcelStatus.REGISTERED,
    },
    months_confirmed={
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "sept": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    },
    today=("today",),
    yesterday=("yesterday",),
    tomorrow=("tomorrow",),
    # "Delivered Saturday", within the past week.
    past_weekdays=(
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ),
    order_placed=("Order placed",),
    delivery_by=("Delivery By",),
    tracking_id_labels=("Tracking ID", "Tracking-ID", "TrackingID"),
    unnamed_carrier_headers=("delivery info",),
    stops_away=("stop away", "stops away"),
    events={
        "package arrived at an amazon facility": ParcelStatus.IN_TRANSIT,
        "package departed an amazon facility": ParcelStatus.IN_TRANSIT,
        "parcel arrived at a carrier facility": ParcelStatus.IN_TRANSIT,
        "package left the courier facility": ParcelStatus.IN_TRANSIT,
        "package arrived at the final delivery station": ParcelStatus.IN_TRANSIT,
        "out for delivery": ParcelStatus.OUT_FOR_DELIVERY,
        "delivered to customer": ParcelStatus.DELIVERED,
        "delivered to letterbox": ParcelStatus.DELIVERED,
    },
    delivered_events=("delivered",),
    arrival=(
        # "Arriving tomorrow", "Arriving Oct 27", "Arriving Oct 27-29",
        # "Arriving Oct 27-Nov 2". A timed "Arriving today 11:00 AM - ..." is
        # the delivery window's business and does not match.
        r"arriving\s+(?:(?P<rel>today|tomorrow)|(?P<m1>[a-z]+)\s+(?P<d1>\d{1,2})"
        r"(?:\s*-\s*(?:(?P<m2>[a-z]+)\s+)?(?P<d2>\d{1,2}))?)",
    ),
)

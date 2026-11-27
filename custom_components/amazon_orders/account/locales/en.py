"""English: amazon.com, amazon.co.uk, amazon.ca and any account set to English."""

from ...const import ParcelStatus
from .base import SKIP, Locale

LOCALE = Locale(
    status_confirmed={
        "delivered": ParcelStatus.DELIVERED,
        "arriving": ParcelStatus.IN_TRANSIT,
        # #62: a revised estimate, "Now arriving tomorrow" included.
        "now arriving": ParcelStatus.IN_TRANSIT,
        # #62: shown minutes before the parcel was delivered.
        "arriving now": ParcelStatus.OUT_FOR_DELIVERY,
        # #62: an Amazon Now order on its way; "Delivered in 12 mins" follows.
        "amazon now delivery": ParcelStatus.IN_TRANSIT,
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
    # "Arriving Wednesday" (#40): the next such day, today included.
    upcoming_weekdays=(
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ),
    order_placed=("Order placed",),
    # #62: amazon.com's headers for Amazon's own fleet.
    delivery_by=("Delivery By", "Delivery facilitated by", "Shipped with"),
    tracking_id_labels=("Tracking ID", "Tracking-ID", "TrackingID"),
    unnamed_carrier_headers=("delivery info",),
    stops_away=("stop away", "stops away"),
    next_stop=("You're the next stop", "You are the next stop"),
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
    # #62: an Amazon Now order, delivered minutes after it was placed.
    undated_delivered=(r"delivered\s+in\s+\d+\s+mins?",),
    arrival=(
        # "Arriving tomorrow", "Arriving Oct 27", "Arriving Oct 27-29",
        # "Arriving Oct 27-Nov 2". A timed "Arriving today 11:00 AM - ..." does
        # not match: window_day reads it.
        r"arriving\s+(?:(?P<rel>today|tomorrow)|(?P<m1>[a-z]+)\s+(?P<d1>\d{1,2})"
        r"(?:\s*[-–]\s*(?:(?P<m2>[a-z]+)\s+)?(?P<d2>\d{1,2}))?)",
        # Day first, as amazon.de writes in English (#40): "Arriving 20 November",
        # "Arriving 3-5 November".
        r"arriving\s+(?P<d1>\d{1,2})(?:\s*[-–]\s*(?P<d2>\d{1,2}))?\s+(?P<m1>[a-z]+)",
        # "Arriving 20 November - 18 December" (#40).
        r"arriving\s+(?P<d1>\d{1,2})\s+(?P<m1>[a-z]+)\s*[-–]\s*(?P<d2>\d{1,2})"
        r"\s+(?P<m2>[a-z]+)",
        # "Arriving now" (#62): the driver is about to deliver, so today.
        r"arriving\s+(?P<rel>now)",
        # "Arriving Wednesday" (#40).
        r"arriving\s+(?P<weekday>[a-z]+)",
    ),
    arrival_leads=("arriving",),
    window_day=(
        # "Now arriving today 3 pm - 6 pm" (#39): amazon.de in English sends
        # no countdown.
        r"(?:now\s+)?arriving\s+(?P<rel>today|tomorrow)\b",
    ),
    # "Arriving overnight 1 AM – 6 AM" (#62): the coming night, so the next
    # time the window's end comes round.
    overnight=(r"arriving\s+overnight\b",),
)

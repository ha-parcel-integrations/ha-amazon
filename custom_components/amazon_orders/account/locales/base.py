"""The shape every language file fills in."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from ...const import ParcelStatus

# A line that must never count as incoming (cancelled, returned, refunded).
SKIP: Final = "skip"

Kind = ParcelStatus | str


@dataclass(frozen=True)
class Locale:
    """The wording Amazon uses in one language.

    "Confirmed" holds only text a real page has shown; "plausible" is how
    Amazon is expected to phrase it, and resolving through it asks once for
    confirmation. Every language is always tried, whatever the storefront:
    an account can be set to any language.
    """

    # Order-line status, matched on the start of the text, lower case.
    status_confirmed: dict[str, Kind] = field(default_factory=dict)
    status_plausible: dict[str, Kind] = field(default_factory=dict)
    # Month names and abbreviations, lower case, 1 = January.
    months_confirmed: dict[str, int] = field(default_factory=dict)
    months_plausible: dict[str, int] = field(default_factory=dict)
    today: tuple[str, ...] = ()
    yesterday: tuple[str, ...] = ()
    tomorrow: tuple[str, ...] = ()
    # Monday first: a delivered line's past weekday ("Livré jeudi"), and the
    # coming weekday an in-flight line names ("Zustellung: Freitag").
    past_weekdays: tuple[str, ...] = ()
    upcoming_weekdays: tuple[str, ...] = ()
    # Exactly as written on the page, before the order date.
    order_placed: tuple[str, ...] = ()
    # Exactly as written on the page, before the courier's name.
    delivery_by: tuple[str, ...] = ()
    tracking_id_labels: tuple[str, ...] = ()
    # Delivery-card headers that name no courier, lower case.
    unnamed_carrier_headers: tuple[str, ...] = ()
    # The live map's callout after the number: "3 stops away".
    stops_away: tuple[str, ...] = ()
    # The whole callout when this parcel is the driver's next stop.
    next_stop: tuple[str, ...] = ()
    # Timeline messages, lower case, without trailing "." or "!". Confirmed only.
    events: dict[str, ParcelStatus] = field(default_factory=dict)
    # A timeline message starting with one of these is a delivery.
    delivered_events: tuple[str, ...] = ()
    # Regular expressions (case-insensitive, matching the whole order line)
    # for the day a parcel arrives. Named groups: ``rel`` (a today/tomorrow
    # word), ``weekday``, or ``d1``/``m1`` with optional ``d2``/``m2``. The
    # first pattern that matches decides.
    arrival: tuple[str, ...] = ()
    # An order line starting with one of these (lower case) names its arrival;
    # one no arrival or window-day pattern reads warns once.
    arrival_leads: tuple[str, ...] = ()
    # Regular expressions (case-insensitive, matching the start of a timed
    # promise) whose ``rel`` group names the window's day.
    window_day: tuple[str, ...] = ()

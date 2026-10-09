"""Constants for the Amazon parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "amazon_orders"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Every value not listed here must come back as a literal ``None`` from
# normalize_parcel() — never omit the key. The docs site's carrier comparison
# table is generated straight from this constant.
#
# Amazon exposes no weight, dimensions or pickup point on the pages we read.
# ``url`` is the ship-track page (or the order page when a shipment has no
# tracking page), ``history`` is the timestamped timeline on ship-track.
CAPABILITIES = frozenset({"url", "history"})

# Fields whose support is not confirmed yet — typically a carrier built without
# a real parcel to check against. Leave this empty once the open questions are
# answered. A field is in exactly one of three states: in CAPABILITIES (seen
# populated), in PENDING_CAPABILITIES (docs site shows "awaiting data"), or in
# neither (the API never exposes it). Pending fields must still come back as a
# literal ``None`` from normalize_parcel() until they are confirmed and moved
# into CAPABILITIES. A multi-backend carrier declares
# PENDING_CAPABILITIES_BY_VARIANT with the same keys as its
# CAPABILITIES_BY_VARIANT instead (omit backends with nothing pending).
PENDING_CAPABILITIES: frozenset[str] = frozenset({"delivery_window"})

# If this carrier ever grows a second backend with a genuinely different
# payload shape (a country-specific API, not just a config option), replace
# the single CAPABILITIES above with a CAPABILITIES_BY_VARIANT dict instead:
#
#   CAPABILITIES_BY_VARIANT = {
#       "Germany": frozenset({"pickup_point", "url", "history"}),
#       "Other": frozenset({"weight", "dimensions", "delivery_window",
#                            "pickup_point", "url", "history"}),
#   }
#
# Key order is display order on the docs site's comparison table; label each
# key exactly as the carrier's own country/backend selector does. The docs
# site's generator accepts either shape — don't declare both. Do not add this
# preemptively: a single-backend carrier (the common case) keeps the flat
# CAPABILITIES above.

# One config entry per Amazon account per country. The picker offers these storefronts.
CONF_COUNTRY = "country"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_SERIAL = "device_serial"
CONF_LANDING_URL = "landing_url"

COUNTRY_DOMAINS = (
    "amazon.nl",
    "amazon.de",
    "amazon.fr",
    "amazon.co.uk",
    "amazon.com",
    "amazon.com.be",
    "amazon.es",
    "amazon.it",
    "amazon.ie",
    "amazon.se",
    "amazon.pl",
    "amazon.ca",
    "amazon.com.au",
    "amazon.co.jp",
    "amazon.in",
    "amazon.com.mx",
    "amazon.com.br",
)

# Sign-in page language per storefront. Anything missing signs in in English,
# which is deliberate for storefronts that serve more than one language.
SIGN_IN_LANGUAGE = {
    "amazon.nl": "nl_NL",
    "amazon.de": "de_DE",
    "amazon.fr": "fr_FR",
    "amazon.co.uk": "en_GB",
    "amazon.es": "es_ES",
    "amazon.it": "it_IT",
    "amazon.ca": "en_CA",
    "amazon.com.au": "en_AU",
    "amazon.co.jp": "ja_JP",
    "amazon.in": "en_IN",
    "amazon.com.mx": "es_MX",
    "amazon.com.br": "pt_BR",
    "amazon.se": "sv_SE",
    "amazon.pl": "pl_PL",
    "amazon.com": "en_US",
}

ORDERS_PATHS = ("/your-orders/orders", "/gp/css/order-history")

# Fan-out caps. Every shipment costs up to two page loads, so a poll only
# follows orders that are still moving or were delivered recently, and never
# loads more than MAX_TRACK_LOADS ship-track pages in one cycle.
MAX_TRACK_LOADS = 10
DELIVERED_LOOKBACK_DAYS = 7
REQUEST_PAUSE_SECONDS = 1.0

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see scaffold/CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one active parcel
# is out_for_delivery within HOT_LOOKAHEAD_HOURS of its planned_from (or has
# no planned_from at all); mid = anything else still in flight. Unlike the
# barcode-based model, the account-based coordinator never fully stops — the
# mid-tier poll is also how a new shipment gets discovered.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
# Nothing in flight: each poll still costs a token refresh, a cookie exchange
# and an orders page, so idle accounts are read less often.
IDLE_INTERVAL_MINUTES = 120
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default: it is a large attribute, and on carriers that
# need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20

"""Diagnostics support for the Amazon parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AmazonConfigEntry
from .const import CONF_DEVICE_SERIAL, CONF_REFRESH_TOKEN

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address, an order or a specific parcel, and every
# credential. Over-redacting is cheap; under-redacting leaks a user's account
# into a GitHub thread.
TO_REDACT = {
    # credentials
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_SERIAL,
    "access_token",
    "cookies",
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # fields of the shipment record
    "tracking_id",
    "order_id",
    "shipment_id",
    "items",
    "pop_path",
    "track_path",
    "page_state",
    "order_status_note",
    "location",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmazonConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Amazon config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }

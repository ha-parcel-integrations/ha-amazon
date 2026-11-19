"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry — and because the device is named per account here, an
account-based integration can have several.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_COUNTRY, DOMAIN, ORDERS_PATHS

ATTRIBUTION = "Data provided by Amazon"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this account.

    The storefront is part of the name, so two configured countries do not
    produce two indistinguishable devices. Entities inherit it via
    ``has_entity_name``, yielding names like "Amazon (amazon.nl) Incoming parcels".
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Amazon ({entry.title})",
        manufacturer="Amazon",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"https://www.{entry.data[CONF_COUNTRY]}{ORDERS_PATHS[0]}",
    )

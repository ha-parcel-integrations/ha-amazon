"""Amazon parcel tracker custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account.client import AmazonClient
from .account.coordinator import AmazonCoordinator
from .const import CONF_COUNTRY, CONF_REFRESH_TOKEN, PLATFORMS
from .handoff import HandOff

_LOGGER = logging.getLogger(__name__)


@dataclass
class AmazonData:
    """Runtime data attached to an Amazon config entry."""

    client: AmazonClient
    coordinator: AmazonCoordinator
    session: aiohttp.ClientSession


type AmazonConfigEntry = ConfigEntry[AmazonData]


async def async_setup_entry(
    hass: HomeAssistant, entry: AmazonConfigEntry
) -> bool:
    """Set up Amazon from a config entry."""
    # Each config entry needs its own cookie jar, or two storefronts overwrite
    # each other's cookies in the shared session. The connector is reused
    # (connector_owner=False) so this stays cheap.
    session = aiohttp.ClientSession(
        connector=async_get_clientsession(hass).connector,
        connector_owner=False,
        cookie_jar=aiohttp.CookieJar(),
    )
    client = AmazonClient(
        entry.data[CONF_COUNTRY], entry.data[CONF_REFRESH_TOKEN], session
    )
    coordinator = AmazonCoordinator(hass, client, entry)

    try:
        # Fetch initial data here, before forwarding to platforms. Raising
        # ConfigEntryNotReady / ConfigEntryAuthFailed from a forwarded platform
        # is too late for HA to catch cleanly (it logs a warning and
        # half-sets-up the entry); doing the first refresh here lets a
        # transient failure fail the whole entry so HA retries it with
        # backoff, and an expired sign-in start reauth.
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Without this, every setup retry leaks a session.
        await session.close()
        raise

    entry.runtime_data = AmazonData(
        client=client, coordinator=coordinator, session=session
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await session.close()
        raise

    if hass.state is not CoreState.running:
        # During startup a carrier may not have registered its services yet,
        # which would read as "not installed" and strand the parcel here.
        async def _hand_off(_: Event) -> None:
            await coordinator.async_hand_off_after_start()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _hand_off)
        )

    # No entry.add_update_listener: the options flow calls
    # async_schedule_reload itself. Combining an update listener with a
    # reload-on-update flow is deprecated and becomes an error in HA 2026.12+.
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AmazonConfigEntry
) -> bool:
    """Unload an Amazon config entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.session.close()
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: AmazonConfigEntry) -> None:
    """Forget which parcels this entry handed to carrier integrations."""
    await HandOff(hass, entry.entry_id, entry.options).async_remove()

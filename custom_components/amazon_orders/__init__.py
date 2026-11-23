"""Amazon parcel tracker custom component for Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started

from .account.client import AmazonClient
from .account.coordinator import AmazonCoordinator, business_lines_store
from .const import CONF_COUNTRY, CONF_REFRESH_TOKEN, DOMAIN, PLATFORMS
from .contents import ContentsStore
from .handoff import HandOff, async_clear_issues
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


@dataclass
class AmazonData:
    """Runtime data attached to an Amazon config entry."""

    client: AmazonClient
    coordinator: AmazonCoordinator
    session: aiohttp.ClientSession


type AmazonConfigEntry = ConfigEntry[AmazonData]


async def async_setup_entry(hass: HomeAssistant, entry: AmazonConfigEntry) -> bool:
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
    await coordinator.contents_store.async_load()

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

    async_setup_services(hass)

    if hass.state is not CoreState.running:
        # During startup a carrier may not have registered its services yet,
        # which would read as "not installed" and strand the parcel here.
        # async_at_started, not bus.async_listen_once: unsubscribing a
        # one-time listener that already fired logs an error on reload.
        async def _hand_off(_: HomeAssistant) -> None:
            await coordinator.async_hand_off_after_start()

        entry.async_on_unload(async_at_started(hass, _hand_off))

    # No entry.add_update_listener: the options flow is an
    # OptionsFlowWithReload, which may not be combined with one.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmazonConfigEntry) -> bool:
    """Unload an Amazon config entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.session.close()
        # The action is shared by every account, so it goes with the last one.
        if not any(
            other.state is ConfigEntryState.LOADED
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
        ):
            async_unload_services(hass)
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: AmazonConfigEntry) -> None:
    """Forget what this entry stored: hand-offs, Business lines, contents, issues."""
    await HandOff(hass, entry.entry_id, entry.options).async_remove()
    await business_lines_store(hass, entry.entry_id).async_remove()
    await ContentsStore(hass, entry.entry_id).async_remove()
    # The carrier issues are shared by every entry, so only the last one
    # removed takes them along.
    if not any(
        other.entry_id != entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        async_clear_issues(hass)

"""Tests for Amazon setup and unload."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.errors import (
    AmazonApiError,
    AmazonAuthError,
)
from custom_components.amazon_orders.const import (
    CONF_COUNTRY,
    CONF_DEVICE_SERIAL,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .payloads import ACTIVE_CODE, COUNTRY, active_record

PARCELS = "custom_components.amazon_orders.account.client.AmazonClient.async_get_parcels"


def _entry(country: str = COUNTRY) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=country,
        unique_id=country,
        data={
            CONF_COUNTRY: country,
            CONF_REFRESH_TOKEN: "refresh",
            CONF_DEVICE_SERIAL: "SERIAL",
        },
    )


async def test_setup_and_unload(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(PARCELS, new=AsyncMock(return_value=[active_record()])):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    incoming = hass.states.get("sensor.amazon_amazon_nl_incoming_parcels")
    assert incoming is not None
    assert incoming.state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_two_countries_set_up_independently(hass):
    first, second = _entry(), _entry("amazon.de")
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    with patch(PARCELS, new=AsyncMock(return_value=[])):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

    assert second.state is ConfigEntryState.LOADED

    assert first.runtime_data.client is not second.runtime_data.client
    assert first.runtime_data.session is not second.runtime_data.session
    assert first.runtime_data.client._domain == "amazon.nl"
    assert second.runtime_data.client._domain == "amazon.de"


async def test_rejected_sign_in_starts_reauth(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(PARCELS, new=AsyncMock(side_effect=AmazonAuthError("HTTP 400"))):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


@pytest.mark.parametrize(
    "error",
    [AmazonApiError("HTTP 500"), aiohttp.ClientError("boom")],
)
async def test_outage_retries_instead_of_reauth(hass, error):
    """A 5xx must retry with backoff — never push the user into reauth."""
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(PARCELS, new=AsyncMock(side_effect=error)):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_failed_platform_setup_closes_the_session(hass):
    """Every failed-setup path must close the per-entry session, or each retry
    leaks one."""
    entry = _entry()
    entry.add_to_hass(hass)

    with (
        patch(PARCELS, new=AsyncMock(return_value=[active_record()])),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=RuntimeError("platform blew up")),
        ),
        patch("aiohttp.ClientSession.close", new=AsyncMock()) as close,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    close.assert_awaited()


async def test_per_parcel_sensor_spawn_and_remove(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    parcels = AsyncMock(return_value=[active_record()])
    with patch(PARCELS, new=parcels):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
        )

        # The next poll returns a different parcel: the summary sensor spawns a
        # new per-parcel sensor and removes the stale one via the registry.
        parcels.return_value = [active_record("AMZNL000000000222")]
        await entry.runtime_data.coordinator.async_request_refresh()
        await hass.async_block_till_done()

        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_AMZNL000000000222"
        )
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_{ACTIVE_CODE}"
            )
            is None
        )


async def test_stale_parcel_sensors_are_swept_on_setup(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_AMZNL000000009999",
        config_entry=entry,
    )

    with patch(PARCELS, new=AsyncMock(return_value=[active_record()])):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None


async def test_unload_keeps_the_session_when_platforms_refuse_to_unload(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(PARCELS, new=AsyncMock(return_value=[])):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        assert not await hass.config_entries.async_unload(entry.entry_id)

    assert not entry.runtime_data.session.closed
    await entry.runtime_data.coordinator.async_shutdown()
    await entry.runtime_data.session.close()


async def test_unreadable_line_is_not_counted_by_the_incoming_sensor(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    unreadable = active_record("AMZNL000000000777")
    unreadable.update(
        tracking_id=None, track_path=None, milestone=None, events=[],
        order_status="Gibberish", shipment_id="SHIPunread1",
    )

    with patch(PARCELS, new=AsyncMock(return_value=[active_record(), unreadable])):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.amazon_amazon_nl_incoming_parcels").state == "1"

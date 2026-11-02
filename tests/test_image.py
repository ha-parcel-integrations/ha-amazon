"""Tests for the delivery-photo image entities and how events reach them."""

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.errors import AmazonApiError
from custom_components.amazon_orders.const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .fakes import fake_client
from .payloads import (
    ACTIVE_CODE,
    COUNTRY,
    DELIVERED_CODE,
    FAKE_PHOTO_URL,
    active_record,
    delivered_record,
)

DELIVERED_EVENT = f"{DOMAIN}_parcel_delivered"
PHOTO = b"\xff\xd8photo"


def _client(*records: dict):
    client = fake_client()
    client.async_get_parcels.return_value = list(records)
    client.async_fetch_photo.return_value = (PHOTO, "image/webp")
    client.delivery_photo_url.side_effect = lambda shipment: (
        FAKE_PHOTO_URL if shipment[1] == delivered_record()["shipment_id"] else None
    )
    return client


async def _setup(hass, client) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=COUNTRY,
        data={CONF_COUNTRY: COUNTRY, CONF_REFRESH_TOKEN: "token"},
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.amazon_orders.AmazonClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _photo_entities(hass, entry) -> list[er.RegistryEntry]:
    return [
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain == "image"
    ]


async def test_a_delivered_parcel_with_a_photo_gets_an_image_entity(hass, hass_client):
    client = _client(active_record(), delivered_record())
    entry = await _setup(hass, client)

    entities = _photo_entities(hass, entry)
    assert [entity.unique_id for entity in entities] == [
        f"{entry.entry_id}_delivery_photo_{DELIVERED_CODE}"
    ]
    http = await hass_client()
    response = await http.get(f"/api/image_proxy/{entities[0].entity_id}")
    assert response.status == 200
    assert await response.read() == PHOTO
    assert response.content_type == "image/webp"
    client.async_fetch_photo.assert_awaited_once_with(FAKE_PHOTO_URL)


async def test_the_photo_needs_a_signed_in_user(hass, hass_client_no_auth):
    entry = await _setup(hass, _client(delivered_record()))
    entity_id = _photo_entities(hass, entry)[0].entity_id

    http = await hass_client_no_auth()
    response = await http.get(f"/api/image_proxy/{entity_id}")
    assert response.status in (401, 403)


async def test_no_photo_no_entity(hass):
    client = _client(active_record())
    entry = await _setup(hass, client)
    assert _photo_entities(hass, entry) == []
    client.async_fetch_photo.assert_not_awaited()


async def test_the_photo_is_there_when_the_delivered_event_fires(hass):
    """An automation on the event loads the image straight away."""
    client = _client(active_record())
    entry = await _setup(hass, client)
    coordinator = entry.runtime_data.coordinator
    assert _photo_entities(hass, entry) == []
    seen: list[tuple] = []

    @callback
    def _on_delivered(event):
        # A callback runs as the event fires, not some time after.
        entity_id = event.data["raw"]["delivery_photo_entity_id"]
        seen.append(
            (
                hass.states.get(entity_id) is not None,
                coordinator.photos[ACTIVE_CODE].content,
            )
        )

    hass.bus.async_listen(DELIVERED_EVENT, _on_delivered)
    delivered = delivered_record(ACTIVE_CODE)
    delivered["shipment_id"] = delivered_record()["shipment_id"]
    # The same barcode goes from out for delivery to delivered.
    client.async_get_parcels.return_value = [delivered]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert seen == [(True, PHOTO)]


async def test_the_link_stays_out_of_attributes_and_events(hass):
    record = active_record()
    client = _client(record)
    entry = await _setup(hass, client)
    fired = []
    hass.bus.async_listen(DELIVERED_EVENT, fired.append)
    delivered = delivered_record(ACTIVE_CODE)
    delivered["shipment_id"] = delivered_record()["shipment_id"]
    client.async_get_parcels.return_value = [delivered]
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert fired
    assert "amazonaws" not in str(fired[0].data)
    for state in hass.states.async_all():
        assert "amazonaws" not in str(state.attributes)


async def test_an_event_without_a_photo_names_no_entity(hass):
    client = _client(active_record())
    client.delivery_photo_url.side_effect = None
    client.delivery_photo_url.return_value = None
    entry = await _setup(hass, client)
    fired = []
    hass.bus.async_listen(DELIVERED_EVENT, fired.append)
    client.async_get_parcels.return_value = [
        {**delivered_record(ACTIVE_CODE), "delivery_photo_entity_id": None}
    ]
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert fired[0].data["raw"]["delivery_photo_entity_id"] is None


async def test_a_parcel_that_leaves_the_list_loses_its_entity(hass):
    client = _client(delivered_record())
    entry = await _setup(hass, client)
    assert _photo_entities(hass, entry)

    client.async_get_parcels.return_value = []
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _photo_entities(hass, entry) == []


async def test_a_partly_read_list_keeps_the_entity_of_an_unread_parcel(hass):
    """After a restart the capped fan-out may not have reached the parcel yet."""
    client = _client(delivered_record())
    entry = await _setup(hass, client)

    client.listing_complete = False
    client.async_get_parcels.return_value = []
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _photo_entities(hass, entry)


async def test_the_photo_is_downloaded_once(hass):
    client = _client(delivered_record())
    entry = await _setup(hass, client)
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    client.async_fetch_photo.assert_awaited_once()


async def test_a_failed_download_keeps_the_entity_and_tries_again(hass, hass_client):
    client = _client(delivered_record())
    client.async_fetch_photo = AsyncMock(
        side_effect=[aiohttp.ClientError(), AmazonApiError("HTTP 403"), (PHOTO, None)]
    )
    entry = await _setup(hass, client)
    entities = _photo_entities(hass, entry)
    assert entities

    http = await hass_client()
    url = f"/api/image_proxy/{entities[0].entity_id}"
    assert (await http.get(url)).status != 200
    response = await http.get(url)
    assert response.status == 200
    assert await response.read() == PHOTO


async def test_a_gone_photo_serves_nothing(hass, hass_client):
    client = _client(delivered_record())
    entry = await _setup(hass, client)
    coordinator = entry.runtime_data.coordinator
    entity_id = _photo_entities(hass, entry)[0].entity_id

    coordinator.photos = {}
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"
    http = await hass_client()
    assert (await http.get(f"/api/image_proxy/{entity_id}")).status != 200

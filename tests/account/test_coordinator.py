"""Tests for the Amazon coordinator: fetching and events.

The parcel mapping itself is covered by ``test_parcels.py``.
"""
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.coordinator import AmazonCoordinator
from custom_components.amazon_orders.account.errors import AmazonAuthError
from custom_components.amazon_orders.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    ParcelStatus,
)

from ..payloads import (
    ACTIVE_CODE,
    COUNTRY,
    active_record,
    delivered_record,
    in_transit_record,
)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=COUNTRY,
        unique_id=COUNTRY,
        data={"country": COUNTRY, "refresh_token": "token", "device_serial": "SERIAL"},
        # Keep-most-recent-100 so the delivered-retention filter never trims
        # the (old, fixed-date) sample parcels these tests assert on.
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


async def test_update_splits_active_and_delivered(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_record(), delivered_record()]
    coordinator = AmazonCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert [parcel["barcode"] for parcel in data] == [ACTIVE_CODE]
    assert len(coordinator.delivered) == 1
    assert coordinator.last_success_time is not None


async def test_update_handles_an_empty_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = []
    coordinator = AmazonCoordinator(hass, client, entry)

    assert await coordinator._async_update_data() == []


async def test_expired_session_triggers_reauth(hass):
    """An expired session must start reauth, not retry forever."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = AmazonAuthError("HTTP 401")
    coordinator = AmazonCoordinator(hass, client, entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


async def test_first_refresh_fires_nothing(hass):
    """Otherwise every restart floods the user with "registered" events."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_record()]
    coordinator = AmazonCoordinator(hass, client, entry)

    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_event_carries_device_id(hass):
    from homeassistant.helpers import device_registry as dr

    entry = _entry()
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [in_transit_record()]
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events[0].data["device_id"] == device.id


async def test_fires_status_changed_event(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [in_transit_record()]
    await coordinator._async_update_data()  # first refresh: suppressed
    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_delivery_fires_delivered_event_and_not_status_changed(hass):
    """The hop to delivered fires exactly one, dedicated event."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)

    delivered = []
    changed = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e))
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: changed.append(e)
    )

    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = [delivered_record(ACTIVE_CODE)]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0].data["status"] == ParcelStatus.DELIVERED


async def test_no_events_for_parcel_first_seen_delivered(hass):
    """A parcel already delivered when it first appears fires nothing."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: fired.append(e))

    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()  # first refresh seeds the state
    client.async_get_parcels.return_value = [active_record(), delivered_record()]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_fires_registered_event_for_new_parcel(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()  # first refresh: suppressed
    client.async_get_parcels.return_value = [
        active_record(),
        active_record("AMZNL000000000888"),
    ]
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == "AMZNL000000000888"


def _with_window(monkeypatch, window: dict[str, str | None]):
    """Amazon reports no window, so inject one to exercise the shared event path."""
    from custom_components.amazon_orders.account import coordinator as module

    original = module.normalize_parcel

    def patched(raw, *, include_history=False):
        parcel = original(raw, include_history=include_history)
        parcel["planned_from"] = window["from"]
        parcel["planned_to"] = window["to"]
        return parcel

    monkeypatch.setattr(module, "normalize_parcel", patched)


async def test_fires_delivery_time_changed_event(hass, monkeypatch):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)
    window = {"from": "2026-04-29T13:00:00Z", "to": "2026-04-29T15:00:00Z"}
    _with_window(monkeypatch, window)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()  # first refresh: suppressed

    window.update({"from": "2026-04-29T16:00:00Z", "to": "2026-04-29T18:00:00Z"})
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["new_planned_from"] == "2026-04-29T16:00:00Z"


async def test_losing_the_eta_is_silent(hass, monkeypatch):
    """value -> null just means the carrier lost the window; not worth an alert."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)
    window = {"from": "2026-04-29T13:00:00Z", "to": "2026-04-29T15:00:00Z"}
    _with_window(monkeypatch, window)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = [active_record()]
    await coordinator._async_update_data()

    window.update({"from": None, "to": None})
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []


async def test_delivered_codes_are_always_empty(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = AmazonCoordinator(hass, AsyncMock(), entry)
    assert coordinator.delivered_codes == set()


async def test_device_id_is_resolved_once(hass):
    from homeassistant.helpers import device_registry as dr

    entry = _entry()
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    coordinator = AmazonCoordinator(hass, AsyncMock(), entry)

    assert coordinator._device_id() == device.id
    dr.async_get(hass).async_remove_device(device.id)
    assert coordinator._device_id() == device.id  # cached


async def test_a_record_without_any_key_fires_no_events(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = AmazonCoordinator(hass, client, entry)
    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: fired.append(e))

    client.async_get_parcels.return_value = []
    await coordinator._async_update_data()
    keyless = active_record()
    keyless["tracking_id"] = None
    keyless["shipment_id"] = None
    client.async_get_parcels.return_value = [keyless]
    data = await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(data) == 1 and data[0]["barcode"] is None
    assert fired == []

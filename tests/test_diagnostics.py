"""Tests for Amazon diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.amazon_orders.account.parcels import normalize_parcel
from custom_components.amazon_orders.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .payloads import (
    ACTIVE_CODE,
    FAKE_CUSTOMER,
    ORDER_ID,
    active_record,
    delivered_record,
)

REDACTED = "**REDACTED**"


def _entry(data: list[dict], delivered: list[dict]) -> MagicMock:
    entry = MagicMock()
    entry.data = {
        "country": "amazon.nl",
        "refresh_token": "Atnr|secret-refresh",
        "device_serial": "SECRETSERIAL",
    }
    entry.options = {"include_history": True}
    coordinator = entry.runtime_data.coordinator
    coordinator.current_tier_minutes = 15
    coordinator.update_interval = timedelta(minutes=15)
    coordinator.data = data
    coordinator.delivered = delivered
    coordinator.delivered_codes = set()
    return entry


async def test_credentials_never_survive_diagnostics(hass):
    result = await async_get_config_entry_diagnostics(hass, _entry([], []))

    assert result["entry_data"] == {
        "country": "amazon.nl",
        "refresh_token": REDACTED,
        "device_serial": REDACTED,
    }
    assert "secret-refresh" not in str(result)
    assert "SECRETSERIAL" not in str(result)


async def test_diagnostics_redacts_the_parcel_and_its_record(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    active = normalize_parcel(active_record(), include_history=True)
    delivered = normalize_parcel(delivered_record())

    result = await async_get_config_entry_diagnostics(
        hass, _entry([active], [delivered])
    )

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 1,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    parcel = result["incoming"][0]
    assert parcel["barcode"] == REDACTED
    assert parcel["url"] == REDACTED
    raw = parcel["raw"]
    for key in (
        "tracking_id",
        "order_id",
        "shipment_id",
        "items",
        "pop_path",
        "track_path",
        "page_state",
    ):
        assert raw[key] == REDACTED
    assert result["delivered"][0]["raw"]["events"][2]["location"] == REDACTED
    # non-identifying fields survive, or the diagnostics would be useless
    assert parcel["status"] == "out_for_delivery"
    assert parcel["raw_status"] == "Out for delivery"
    assert raw["carrier_code"] == "DRAGONFLY"
    assert raw["milestone"] == "IN_PROGRESS"
    text = str(result)
    for secret in (ACTIVE_CODE, ORDER_ID, FAKE_CUSTOMER):
        assert secret not in text


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None must be visible, not just absent."""
    entry = _entry([], [])
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }

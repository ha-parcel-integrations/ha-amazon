"""Tests for handing parcels over to the suite's carrier integrations."""
import logging
from unittest.mock import AsyncMock, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.const import (
    CONF_COUNTRY,
    CONF_DEVICE_SERIAL,
    CONF_HAND_OFF,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from custom_components.amazon_orders.handoff import HandOff, issue_id

from .payloads import (
    ACTIVE_CODE,
    COUNTRY,
    active_record,
    delivered_record,
    untracked_record,
)

PARCELS = "custom_components.amazon_orders.account.client.AmazonClient.async_get_parcels"
DRAGONFLY_CODE = ACTIVE_CODE
DHL_CODE = "AMZNL000000000003"


def _record(code: str = ACTIVE_CODE, carrier_code: str | None = "DRAGONFLY") -> dict:
    record = active_record(code)
    record["carrier_code"] = carrier_code
    return record


def _entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=COUNTRY,
        unique_id=COUNTRY,
        data={
            CONF_COUNTRY: COUNTRY,
            CONF_REFRESH_TOKEN: "refresh",
            CONF_DEVICE_SERIAL: "SERIAL",
        },
        options=options,
    )


def _register(hass, domain: str, service: str, error: Exception | None = None):
    """Stand in for a carrier integration's service; returns the call log."""
    calls: list[dict] = []

    async def handler(call):
        calls.append(dict(call.data))
        if error is not None:
            raise error

    hass.services.async_register(domain, service, handler)
    return calls


async def _setup(hass, records, **options):
    entry = _entry(**options)
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=records)
    with patch(PARCELS, new=fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, fetch


async def _refresh(hass, entry, fetch, records):
    fetch.return_value = records
    with patch(PARCELS, new=fetch):
        await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _incoming(hass) -> str:
    return hass.states.get("sensor.amazon_amazon_nl_incoming_parcels").state


async def test_a_parcel_is_handed_off_once_and_hidden_here(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    entry, fetch = await _setup(hass, [_record()])

    assert track == [{"tracking_code": DRAGONFLY_CODE}]
    assert _incoming(hass) == "0"

    # Remembered, so the next poll neither calls the service nor shows it again.
    await _refresh(hass, entry, fetch, [_record()])
    assert len(track) == 1
    assert _incoming(hass) == "0"


async def test_each_carrier_gets_its_own_service_call(hass):
    dragonfly = _register(hass, "dragonfly", "track_parcel")
    dhl = _register(hass, "dhl", "track_parcel")
    colis = _register(hass, "colis_prive", "track_parcel")
    await _setup(
        hass,
        [
            _record(DRAGONFLY_CODE),
            _record(DHL_CODE, "DHL_CONNECT"),
            _record("AMZNL000000000004", "COLIS_PRIVE_BELU"),
        ],
    )
    assert [c["tracking_code"] for c in dragonfly] == [DRAGONFLY_CODE]
    assert [c["tracking_code"] for c in dhl] == [DHL_CODE]
    assert [c["tracking_code"] for c in colis] == ["AMZNL000000000004"]


async def test_missing_carrier_raises_one_issue_per_carrier_domain(hass):
    entry, fetch = await _setup(
        hass,
        [
            _record(DRAGONFLY_CODE),
            _record("AMZNL000000000004"),
            _record(DHL_CODE, "DHL_CONNECT"),
        ],
    )
    registry = ir.async_get(hass)

    # Two Dragonfly parcels, one issue; the parcels stay listed here.
    assert registry.async_get_issue(DOMAIN, issue_id("dragonfly")) is not None
    assert registry.async_get_issue(DOMAIN, issue_id("dhl")) is not None
    assert registry.async_get_issue(DOMAIN, issue_id("colis_prive")) is None
    issue = registry.async_get_issue(DOMAIN, issue_id("dragonfly"))
    assert issue.learn_more_url == (
        "https://ha-parcel-integrations.github.io/carriers/#dragonfly"
    )
    assert issue.translation_placeholders["carrier"] == "Dragonfly"
    assert not issue.is_fixable
    assert _incoming(hass) == "3"

    # Parcels going away does not clear it (an ignored issue must stay ignored).
    await _refresh(hass, entry, fetch, [])
    assert registry.async_get_issue(DOMAIN, issue_id("dragonfly")) is not None

    # It clears when the carrier's service appears, and the parcel moves over.
    track = _register(hass, "dragonfly", "track_parcel")
    await _refresh(hass, entry, fetch, [_record(DRAGONFLY_CODE)])
    assert registry.async_get_issue(DOMAIN, issue_id("dragonfly")) is None
    assert registry.async_get_issue(DOMAIN, issue_id("dhl")) is not None
    assert track == [{"tracking_code": DRAGONFLY_CODE}]


async def test_a_carrier_outside_the_table_is_left_alone(hass):
    """An account-based carrier (no track_parcel) or an unknown one: no call, no issue."""
    entry, _ = await _setup(
        hass, [_record(carrier_code="POSTNL"), _record("AMZNL000000000005", "NOVEL")]
    )
    assert not ir.async_get(hass).issues
    assert _incoming(hass) == "2"
    assert entry.runtime_data.coordinator.handoff._handed == {}


async def test_amazon_logistics_and_stand_in_barcodes_are_never_handed_off(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    stand_in = untracked_record()
    stand_in["carrier_code"] = "DRAGONFLY"
    stand_in["order_status"] = "Shipped"
    own = _record("AMZNL000000000006", None)
    await _setup(hass, [own, stand_in])

    assert track == []
    assert not ir.async_get(hass).issues


async def test_a_delivered_parcel_is_not_handed_off(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    await _setup(hass, [delivered_record()])
    assert track == []


async def test_a_refused_code_warns_once_and_stays_listed(hass, caplog):
    track = _register(
        hass, "dragonfly", "track_parcel", ServiceValidationError("not valid")
    )
    entry, fetch = await _setup(hass, [_record()])
    await _refresh(hass, entry, fetch, [_record()])

    assert len(track) == 1  # not retried until a reload
    assert _incoming(hass) == "1"
    warnings = [r for r in caplog.records if DRAGONFLY_CODE in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "by hand" in warnings[0].getMessage()


async def test_a_vanished_line_is_never_untracked(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    untrack = _register(hass, "dragonfly", "untrack_parcel")
    entry, fetch = await _setup(hass, [_record()])
    assert track

    # A restart reads only part of the order list; that must not untrack.
    await _refresh(hass, entry, fetch, [])
    await _refresh(hass, entry, fetch, [_record()])
    assert untrack == []
    assert len(track) == 1
    assert _incoming(hass) == "0"


async def test_a_long_gone_code_is_forgotten(hass):
    _register(hass, "dragonfly", "track_parcel")
    entry, fetch = await _setup(hass, [_record()])
    handoff = entry.runtime_data.coordinator.handoff

    await _refresh(hass, entry, fetch, [])
    assert DRAGONFLY_CODE in handoff._handed

    handoff._handed[DRAGONFLY_CODE]["since"] = "2020-01-01T00:00:00+00:00"
    await _refresh(hass, entry, fetch, [])
    assert handoff._handed == {}


async def test_an_old_code_still_listed_is_kept(hass):
    _register(hass, "dragonfly", "track_parcel")
    entry, fetch = await _setup(hass, [_record()])
    handoff = entry.runtime_data.coordinator.handoff

    handoff._handed[DRAGONFLY_CODE]["since"] = "2020-01-01T00:00:00+00:00"
    await _refresh(hass, entry, fetch, [_record()])
    assert DRAGONFLY_CODE in handoff._handed

async def test_a_delivered_parcel_stays_with_the_carrier(hass):
    _register(hass, "dragonfly", "track_parcel")
    entry, fetch = await _setup(hass, [_record()])

    await _refresh(hass, entry, fetch, [delivered_record(DRAGONFLY_CODE)])
    assert _incoming(hass) == "0"
    assert DRAGONFLY_CODE in entry.runtime_data.coordinator.handoff._handed

async def test_turning_the_option_off_shows_parcels_again_without_untracking(hass):
    _register(hass, "dragonfly", "track_parcel")
    untrack = _register(hass, "dragonfly", "untrack_parcel")
    entry, fetch = await _setup(hass, [_record()])
    assert _incoming(hass) == "0"

    hass.config_entries.async_update_entry(entry, options={CONF_HAND_OFF: False})
    with patch(PARCELS, new=AsyncMock(return_value=[_record()])):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert untrack == []
    assert _incoming(hass) == "1"
    assert entry.runtime_data.coordinator.handoff._handed == {}

async def test_with_the_option_off_nothing_is_handed_off(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    await _setup(hass, [_record()], **{CONF_HAND_OFF: False})
    assert track == []
    assert not ir.async_get(hass).issues
    assert _incoming(hass) == "1"


async def test_nothing_is_handed_off_before_home_assistant_started(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    hass.set_state(CoreState.starting)
    entry, _ = await _setup(hass, [_record(), _record(DHL_CODE, "DHL_CONNECT")])

    # Neither a call nor an issue: the carrier's service may just be late.
    assert track == []
    assert not ir.async_get(hass).issues
    assert _incoming(hass) == "2"

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert track == [{"tracking_code": DRAGONFLY_CODE}]
    assert _incoming(hass) == "1"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id("dhl")) is not None


async def test_the_remembered_codes_survive_a_reload(hass):
    track = _register(hass, "dragonfly", "track_parcel")
    entry, _ = await _setup(hass, [_record()])
    assert len(track) == 1

    with patch(PARCELS, new=AsyncMock(return_value=[_record()])):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert len(track) == 1
    assert _incoming(hass) == "0"


async def test_removing_the_entry_forgets_the_stored_state(hass):
    _register(hass, "dragonfly", "track_parcel")
    entry, _ = await _setup(hass, [_record()])
    handoff = HandOff(hass, entry.entry_id, entry.options)
    with patch.object(handoff._store, "async_remove", new=AsyncMock()):
        await handoff.async_remove()

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

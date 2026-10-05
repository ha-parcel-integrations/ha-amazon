"""Tests for Section 2.2's dynamic, status-driven polling algorithm
(account-based model).

Pure-function tests for the tiering/scheduling helpers, plus a few
integration checks that ``_async_update_data`` wires them up: the account
never fully stops, and a 429 triggers the Section 3 backoff.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.coordinator import (
    AmazonCoordinator,
    _hottest_tier_minutes,
    _in_quiet_window,
    _next_anchor,
    _next_update_interval,
    _stagger_minutes,
)
from custom_components.amazon_orders.account.errors import AmazonApiError
from custom_components.amazon_orders.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    HOT_INTERVAL_MINUTES,
    IDLE_INTERVAL_MINUTES,
    MID_INTERVAL_MINUTES,
    STAGGER_MINUTES,
)

from ..payloads import COUNTRY, active_record

UTC = timezone.utc


def _out_for_delivery(planned_from: str | None) -> dict:
    return {"status": "out_for_delivery", "planned_from": planned_from}


def _mid(status: str = "in_transit") -> dict:
    return {"status": status, "planned_from": None}


# ---------------------------------------------------------------------------
# _in_quiet_window / _next_anchor
# ---------------------------------------------------------------------------


def test_quiet_window_is_midnight_to_six():
    assert _in_quiet_window(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    assert _in_quiet_window(datetime(2026, 1, 1, 5, 59, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 6, 0, tzinfo=UTC))
    assert not _in_quiet_window(datetime(2026, 1, 1, 23, 59, tzinfo=UTC))


def test_next_anchor_before_six_is_six_today():
    now = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_next_anchor_after_six_is_midnight_tomorrow():
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    assert _next_anchor(now) == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _stagger_minutes
# ---------------------------------------------------------------------------


def test_stagger_is_stable_and_bounded():
    a = _stagger_minutes("entry-1")
    b = _stagger_minutes("entry-1")
    c = _stagger_minutes("entry-2")
    assert a == b
    assert 0 <= a < STAGGER_MINUTES
    assert 0 <= c < STAGGER_MINUTES


# ---------------------------------------------------------------------------
# _hottest_tier_minutes — never None for the account-based model
# ---------------------------------------------------------------------------


def test_tier_is_idle_when_nothing_active():
    assert _hottest_tier_minutes([], datetime(2026, 1, 1, 12, tzinfo=UTC)) == IDLE_INTERVAL_MINUTES


def test_tier_is_mid_for_non_hot_statuses():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [_mid("registered"), _mid("problem"), _mid("returning")]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


def test_tier_is_hot_when_out_for_delivery_without_planned_from():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    parcels = [_mid(), _out_for_delivery(None)]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_hot_when_planned_from_cannot_be_parsed():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert (
        _hottest_tier_minutes([_out_for_delivery("garbage")], now)
        == HOT_INTERVAL_MINUTES
    )


def test_tier_is_hot_within_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(minutes=30)  # inside the 1h lookahead
    parcels = [_out_for_delivery(planned.isoformat())]
    assert _hottest_tier_minutes(parcels, now) == HOT_INTERVAL_MINUTES


def test_tier_is_mid_before_lookahead_of_planned_from():
    planned = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    now = planned - timedelta(hours=3)  # well outside the 1h lookahead
    parcels = [_out_for_delivery(planned.isoformat())]
    assert _hottest_tier_minutes(parcels, now) == MID_INTERVAL_MINUTES


# ---------------------------------------------------------------------------
# _next_update_interval
# ---------------------------------------------------------------------------


def test_daytime_candidate_outside_window_is_tier_plus_stagger():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    stagger = _stagger_minutes("entry-1")
    assert interval == timedelta(minutes=MID_INTERVAL_MINUTES + stagger)


def test_now_inside_quiet_window_jumps_to_next_anchor():
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # an anchor poll itself
    interval = _next_update_interval(now, HOT_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


def test_candidate_landing_in_quiet_window_clamps_to_the_midnight_anchor():
    now = datetime(2026, 1, 1, 23, 50, tzinfo=UTC)
    interval = _next_update_interval(now, MID_INTERVAL_MINUTES, "entry-1")
    assert now + interval == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# integration: never fully stops, and the 429 backoff
# ---------------------------------------------------------------------------


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=COUNTRY,
        unique_id=COUNTRY,
        data={"country": COUNTRY, "refresh_token": "token", "device_serial": "SERIAL"},
        options={
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )


async def test_update_interval_never_none_with_an_empty_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = []
    coordinator = AmazonCoordinator(hass, client, entry)

    await coordinator._async_update_data()

    assert coordinator.update_interval is not None
    assert coordinator.current_tier_minutes == IDLE_INTERVAL_MINUTES


async def test_update_interval_is_hot_for_an_out_for_delivery_parcel(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = [active_record()]
    coordinator = AmazonCoordinator(hass, client, entry)

    await coordinator._async_update_data()

    assert coordinator.current_tier_minutes == HOT_INTERVAL_MINUTES


async def test_429_raises_update_failed_with_retry_after(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = AmazonApiError(
        "HTTP 429", status_code=429, retry_after=120
    )
    coordinator = AmazonCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert excinfo.value.retry_after == 120


async def test_429_backoff_grows_without_a_retry_after_header(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = AmazonApiError(
        "HTTP 429", status_code=429, retry_after=None
    )
    coordinator = AmazonCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed) as first:
        await coordinator._async_update_data()
    with pytest.raises(UpdateFailed) as second:
        await coordinator._async_update_data()

    assert first.value.retry_after is not None
    assert second.value.retry_after > first.value.retry_after


async def test_503_is_backed_off_like_a_429(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = AmazonApiError(
        "HTTP 503", status_code=503, retry_after=30
    )
    coordinator = AmazonCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert excinfo.value.retry_after == 30


async def test_non_429_api_error_still_propagates(hass):
    """The existing 5xx-is-not-caught behaviour must survive Section 3's addition."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = AmazonApiError(
        "HTTP 500", status_code=500
    )
    coordinator = AmazonCoordinator(hass, client, entry)

    with pytest.raises(AmazonApiError):
        await coordinator._async_update_data()

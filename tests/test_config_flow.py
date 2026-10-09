"""Tests for the Amazon config and options flow."""
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.account.auth import DeviceRegistration
from custom_components.amazon_orders.account.errors import (
    AmazonApiError,
    AmazonAuthError,
)
from custom_components.amazon_orders.const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DEVICE_SERIAL,
    CONF_INCLUDE_HISTORY,
    CONF_LANDING_URL,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

COUNTRY = "amazon.nl"
LANDING = (
    "https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=CODE123"
)
REGISTER = "custom_components.amazon_orders.config_flow.register_device"
CUSTOMER = "amzn1.account.TESTACCOUNT1"


def _registration(customer: str | None = CUSTOMER, name: str | None = "Sam"):
    return (
        "api.amazon.nl",
        DeviceRegistration(
            refresh_token="new-refresh",
            access_token="a",
            expires_in=3600,
            customer_id=customer,
            customer_name=name,
        ),
    )


REGISTRATION = _registration()


def _entry(country: str = COUNTRY, unique_id: str | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=country,
        unique_id=unique_id or f"{country}:{CUSTOMER}",
        data={
            CONF_COUNTRY: country,
            CONF_REFRESH_TOKEN: "old-refresh",
            CONF_DEVICE_SERIAL: "OLDSERIAL",
        },
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
            CONF_INCLUDE_HISTORY: False,
        },
    )


async def _to_sign_in(hass, country: str = COUNTRY):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COUNTRY: country}
    )


# ---------------------------------------------------------------------------
# user step
# ---------------------------------------------------------------------------


async def test_user_flow_shows_the_sign_in_link_then_creates_the_entry(hass):
    result = await _to_sign_in(hass)

    assert result["step_id"] == "sign_in"
    url = result["description_placeholders"]["sign_in_url"]
    assert urlparse(url).path == "/ap/signin"
    assert parse_qs(urlparse(url).query)["language"] == ["nl_NL"]
    assert result["description_placeholders"]["country"] == COUNTRY
    assert result["description_placeholders"]["landing_url_prefix"].endswith(
        "/ap/maplanding"
    )

    with patch(REGISTER, new=AsyncMock(return_value=REGISTRATION)) as register:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == f"{COUNTRY} · Sam"
    assert result["result"].unique_id == f"{COUNTRY}:{CUSTOMER}"
    assert result["data"][CONF_COUNTRY] == COUNTRY
    assert result["data"][CONF_REFRESH_TOKEN] == "new-refresh"
    serial = result["data"][CONF_DEVICE_SERIAL]
    assert len(serial) == 32
    assert result["options"][CONF_INCLUDE_HISTORY] is False
    # The code went to registration together with this flow's own serial.
    args = register.await_args.args
    assert args[1] == COUNTRY and args[2] == serial and args[4] == "CODE123"


async def test_pasted_address_without_a_code_asks_again(hass):
    result = await _to_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_LANDING_URL: "https://www.amazon.com/ap/maplanding?x=1"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_url"}


@pytest.mark.parametrize(
    "error,expected",
    [
        (AmazonAuthError("HTTP 400"), "sign_in_failed"),
        (AmazonApiError("HTTP 500"), "cannot_connect"),
        (aiohttp.ClientError("boom"), "cannot_connect"),
    ],
)
async def test_registration_failures_are_told_apart(hass, error, expected):
    """A rejected sign-in and an outage must not look the same to the user."""
    result = await _to_sign_in(hass)
    with patch(REGISTER, new=AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": expected}
    # The same link stays on screen, so the user can retry without starting over.
    assert "sign_in_url" in result["description_placeholders"]


async def _sign_in(hass, registration, country: str = COUNTRY):
    result = await _to_sign_in(hass, country)
    with patch(REGISTER, new=AsyncMock(return_value=registration)):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )


async def test_the_same_account_cannot_be_added_twice(hass):
    _entry().add_to_hass(hass)
    result = await _sign_in(hass, REGISTRATION)
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_a_second_account_on_the_same_country_can_be_added(hass):
    _entry().add_to_hass(hass)
    result = await _sign_in(hass, _registration("amzn1.account.OTHER", None))
    assert result["type"] == "create_entry"
    assert result["title"] == COUNTRY
    assert result["result"].unique_id == f"{COUNTRY}:amzn1.account.OTHER"


async def test_a_sign_in_without_an_account_id_still_adds_an_entry(hass, caplog):
    _entry().add_to_hass(hass)
    result = await _sign_in(hass, _registration(None, None))
    assert result["type"] == "create_entry"
    serial = result["data"][CONF_DEVICE_SERIAL]
    assert result["result"].unique_id == f"{COUNTRY}:{serial}"
    assert "did not name the account" in caplog.text


async def test_another_country_can_be_added_alongside(hass):
    _entry().add_to_hass(hass)
    result = await _to_sign_in(hass, "amazon.de")
    assert result["step_id"] == "sign_in"
    assert parse_qs(urlparse(result["description_placeholders"]["sign_in_url"]).query)[
        "language"
    ] == ["de_DE"]


# ---------------------------------------------------------------------------
# reauth
# ---------------------------------------------------------------------------


async def test_reauth_replaces_the_token_and_serial(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "sign_in"
    assert result["description_placeholders"]["country"] == COUNTRY

    with patch(REGISTER, new=AsyncMock(return_value=REGISTRATION)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-refresh"
    assert entry.data[CONF_DEVICE_SERIAL] != "OLDSERIAL"
    assert entry.data[CONF_COUNTRY] == COUNTRY


async def test_reauth_with_another_account_is_refused(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(REGISTER, new=AsyncMock(return_value=_registration("amzn1.account.OTHER"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )

    assert result["type"] == "abort"
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_REFRESH_TOKEN] == "old-refresh"


async def test_reauth_of_a_country_keyed_entry_adopts_the_account(hass):
    entry = _entry(unique_id=COUNTRY)
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(REGISTER, new=AsyncMock(return_value=REGISTRATION)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == f"{COUNTRY}:{CUSTOMER}"


async def test_reauth_surfaces_a_rejected_sign_in(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(REGISTER, new=AsyncMock(side_effect=AmazonAuthError("HTTP 400"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LANDING_URL: LANDING}
        )

    assert result["errors"] == {"base": "sign_in_failed"}


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


async def test_options_flow_saves_and_reloads(hass):
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    with patch.object(
        hass.config_entries, "async_schedule_reload"
    ) as schedule_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "delivered": {
                    CONF_DELIVERED_FILTER_TYPE: "parcels",
                    CONF_DELIVERED_FILTER_AMOUNT: 5,
                },
                "history": {CONF_INCLUDE_HISTORY: True},
            },
        )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_DELIVERED_FILTER_TYPE: "parcels",
        CONF_DELIVERED_FILTER_AMOUNT: 5,
        CONF_INCLUDE_HISTORY: True,
    }
    # A changed setting only takes effect on reload, so the flow schedules one
    # itself rather than registering an update listener (which is deprecated in
    # combination with reloading).
    schedule_reload.assert_called_once_with(entry.entry_id)

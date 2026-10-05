"""Config flow for the Amazon parcel tracker integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account.auth import (
    LANDING_URL,
    build_sign_in_url,
    extract_authorization_code,
    new_code_verifier,
    new_device_serial,
    register_device,
)
from .account.errors import AmazonApiError, AmazonAuthError
from .const import (
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DEVICE_SERIAL,
    CONF_INCLUDE_HISTORY,
    CONF_LANDING_URL,
    CONF_REFRESH_TOKEN,
    COUNTRY_DOMAINS,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_COUNTRY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_COUNTRY, default=COUNTRY_DOMAINS[0]): (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(COUNTRY_DOMAINS),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        )
    }
)
_SIGN_IN_SCHEMA = vol.Schema({vol.Required(CONF_LANDING_URL): str})


class AmazonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Amazon integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the per-flow sign-in state."""
        self._domain: str = COUNTRY_DOMAINS[0]
        self._serial: str = ""
        self._verifier: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AmazonOptionsFlowHandler:
        """Return the options flow handler."""
        return AmazonOptionsFlowHandler()

    def _start_sign_in(self, domain: str) -> None:
        """Mint the device serial and PKCE verifier for this sign-in."""
        self._domain = domain
        self._serial = new_device_serial()
        self._verifier = new_code_verifier()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which Amazon country to read."""
        if user_input is not None:
            domain = user_input[CONF_COUNTRY]
            await self.async_set_unique_id(domain)
            self._abort_if_unique_id_configured()
            self._start_sign_in(domain)
            return await self.async_step_sign_in()

        return self.async_show_form(step_id="user", data_schema=_COUNTRY_SCHEMA)

    async def async_step_sign_in(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the sign-in link and take the pasted landing URL back."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = extract_authorization_code(user_input[CONF_LANDING_URL])
            if code is None:
                errors["base"] = "invalid_url"
            else:
                try:
                    _, registration = await register_device(
                        async_get_clientsession(self.hass),
                        self._domain,
                        self._serial,
                        self._verifier,
                        code,
                    )
                except AmazonAuthError:
                    errors["base"] = "sign_in_failed"
                except (AmazonApiError, aiohttp.ClientError):
                    errors["base"] = "cannot_connect"
                else:
                    data = {
                        CONF_COUNTRY: self._domain,
                        CONF_REFRESH_TOKEN: registration.refresh_token,
                        CONF_DEVICE_SERIAL: self._serial,
                    }
                    if self.source == "reauth":
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(), data_updates=data
                        )
                    return self.async_create_entry(
                        title=self._domain,
                        data=data,
                        options={
                            CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                            CONF_DELIVERED_FILTER_AMOUNT: (
                                DEFAULT_DELIVERED_FILTER_AMOUNT
                            ),
                            CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                        },
                    )

        return self.async_show_form(
            step_id="sign_in",
            data_schema=_SIGN_IN_SCHEMA,
            errors=errors,
            description_placeholders={
                "sign_in_url": build_sign_in_url(
                    self._domain, self._serial, self._verifier
                ),
                "country": self._domain,
                "landing_url_prefix": LANDING_URL,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after Amazon stopped accepting the stored sign-in."""
        self._start_sign_in(entry_data[CONF_COUNTRY])
        return await self.async_step_sign_in()


class AmazonOptionsFlowHandler(OptionsFlow):
    """Manage delivered retention and history in one sectioned form."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the single sectioned options form."""
        if user_input is not None:
            delivered = user_input["delivered"]
            history = user_input["history"]
            # Reload so a changed history/delivered-retention setting takes
            # effect immediately. No update listener is registered —
            # combining the two is deprecated.
            self.hass.config_entries.async_schedule_reload(
                self.config_entry.entry_id
            )
            return self.async_create_entry(
                title="",
                data={
                    CONF_DELIVERED_FILTER_TYPE: delivered[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        delivered[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(history[CONF_INCLUDE_HISTORY]),
                },
            )

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
                                ),
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["days", "parcels"],
                                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                                    mode=selector.SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Required(
                                CONF_DELIVERED_FILTER_AMOUNT,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    DEFAULT_DELIVERED_FILTER_AMOUNT,
                                ),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1,
                                    max=365,
                                    step=1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

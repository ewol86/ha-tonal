"""Config and options flow for the Tonal integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TonalApi, TonalAuthError, TonalConnectionError, TonalError
from .const import (
    CONF_FETCH_TITLES,
    CONF_REFRESH_TOKEN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class TonalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a Tonal account."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._reauth_entry: ConfigEntry | None = None

    async def _async_validate(self, email: str, password: str) -> tuple[dict, str | None]:
        """Log in and return (userinfo, refresh token)."""
        api = TonalApi(
            async_get_clientsession(self.hass), email=email, password=password
        )
        await api.async_login()
        user = await api.async_get_userinfo()
        return user, api.refresh_token

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and verify them against Tonal."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                user, refresh_token = await self._async_validate(
                    email, user_input[CONF_PASSWORD]
                )
            except TonalAuthError:
                errors["base"] = "invalid_auth"
            except TonalConnectionError:
                errors["base"] = "cannot_connect"
            except TonalError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001 - a config flow must never leak
                _LOGGER.exception("Unexpected error authenticating with Tonal")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(user.get("id") or email))
                self._abort_if_unique_id_configured()

                name = " ".join(
                    part
                    for part in (user.get("firstName"), user.get("lastName"))
                    if part
                )
                return self.async_create_entry(
                    title=name or email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after Tonal stopped accepting the session."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            email = entry.data[CONF_EMAIL]
            try:
                _, refresh_token = await self._async_validate(
                    email, user_input[CONF_PASSWORD]
                )
            except TonalAuthError:
                errors["base"] = "invalid_auth"
            except TonalConnectionError:
                errors["base"] = "cannot_connect"
            except TonalError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TonalOptionsFlow:
        """Return the options flow."""
        return TonalOptionsFlow()


class TonalOptionsFlow(OptionsFlow):
    """Handle the polling options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL_MINUTES, max=MAX_SCAN_INTERVAL_MINUTES),
                ),
                vol.Required(
                    CONF_FETCH_TITLES,
                    default=options.get(CONF_FETCH_TITLES, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

"""Config, subentry and options flows for the Tonal integration.

One config entry represents the trainer. Each person with a Tonal account gets
an "account" subentry under it, with its own credentials, coordinator, device
and sensors.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TonalApi, TonalAuthError, TonalConnectionError, TonalError
from .const import (
    CONF_FETCH_TITLES,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    SUBENTRY_TYPE_ACCOUNT,
)

_LOGGER = logging.getLogger(__name__)

ACCOUNT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def _async_validate(hass, email: str, password: str) -> tuple[dict, str | None]:
    """Log in and return (userinfo, refresh token)."""
    api = TonalApi(async_get_clientsession(hass), email=email, password=password)
    await api.async_login()
    user = await api.async_get_userinfo()
    return user, api.refresh_token


def _account_name(user: dict[str, Any], email: str) -> str:
    """Return a display name for an account."""
    name = " ".join(
        part for part in (user.get("firstName"), user.get("lastName")) if part
    )
    return name or email


def _error_for(err: Exception) -> str:
    """Map an API error onto a form error key."""
    if isinstance(err, TonalAuthError):
        return "invalid_auth"
    if isinstance(err, TonalConnectionError):
        return "cannot_connect"
    return "unknown"


class TonalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the trainer, along with its first account."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry and its first account subentry together."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                user, refresh_token = await _async_validate(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except TonalError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001 - a config flow must never leak
                _LOGGER.exception("Unexpected error authenticating with Tonal")
                errors["base"] = "unknown"
            else:
                user_id = str(user.get("id") or email)
                return self.async_create_entry(
                    title="Tonal",
                    data={},
                    subentries=[
                        {
                            "subentry_type": SUBENTRY_TYPE_ACCOUNT,
                            "title": _account_name(user, email),
                            "unique_id": user_id,
                            "data": {
                                CONF_EMAIL: email,
                                CONF_PASSWORD: user_input[CONF_PASSWORD],
                                CONF_REFRESH_TOKEN: refresh_token,
                                CONF_USER_ID: user_id,
                            },
                        }
                    ],
                )

        return self.async_show_form(
            step_id="user", data_schema=ACCOUNT_SCHEMA, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_TYPE_ACCOUNT: TonalAccountSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TonalOptionsFlow:
        """Return the options flow."""
        return TonalOptionsFlow()


class TonalAccountSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one Tonal account on the trainer.

    Subentry flows cannot do reauth, so ``reconfigure`` doubles as the place to
    re-enter a password after Tonal invalidates a session.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add an account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                user, refresh_token = await _async_validate(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except TonalError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error authenticating with Tonal")
                errors["base"] = "unknown"
            else:
                user_id = str(user.get("id") or email)
                entry = self._get_entry()
                if any(
                    subentry.unique_id == user_id
                    for subentry in entry.subentries.values()
                ):
                    return self.async_abort(reason="already_configured")

                return self.async_create_entry(
                    title=_account_name(user, email),
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                        CONF_USER_ID: user_id,
                    },
                    unique_id=user_id,
                )

        return self.async_show_form(
            step_id="user", data_schema=ACCOUNT_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Re-enter the password for an existing account."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()
        email = subentry.data[CONF_EMAIL]

        if user_input is not None:
            try:
                user, refresh_token = await _async_validate(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except TonalError as err:
                errors["base"] = _error_for(err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error authenticating with Tonal")
                errors["base"] = "unknown"
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=_account_name(user, email),
                    data={
                        **subentry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )


class TonalOptionsFlow(OptionsFlow):
    """Polling options, applied to every account on the trainer."""

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
                    vol.Range(
                        min=MIN_SCAN_INTERVAL_MINUTES, max=MAX_SCAN_INTERVAL_MINUTES
                    ),
                ),
                vol.Required(
                    CONF_FETCH_TITLES,
                    default=options.get(CONF_FETCH_TITLES, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

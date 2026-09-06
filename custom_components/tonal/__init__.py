"""The Tonal integration.

Brings your Tonal workout history into Home Assistant, using the same private
API the Tonal app talks to. Unofficial, and unaffiliated with Tonal Systems, Inc.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import TonalApi
from .const import (
    ATTR_FILE_PATH,
    ATTR_FULL,
    ATTR_GZIP,
    CONF_FETCH_TITLES,
    CONF_REFRESH_TOKEN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    SERVICE_EXPORT_DATA,
)
from .coordinator import TonalCoordinator
from .export import build_export, write_export

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional(ATTR_FILE_PATH): cv.string,
        vol.Optional(ATTR_FULL, default=False): cv.boolean,
        vol.Optional(ATTR_GZIP, default=True): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tonal from a config entry."""
    api = TonalApi(
        async_get_clientsession(hass),
        email=entry.data[CONF_EMAIL],
        password=entry.data.get(CONF_PASSWORD),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
    )

    scan_interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
    coordinator = TonalCoordinator(
        hass,
        entry,
        api,
        scan_interval,
        fetch_titles=entry.options.get(CONF_FETCH_TITLES, True),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded and not _loaded_entries(hass, exclude=entry.entry_id):
        hass.services.async_remove(DOMAIN, SERVICE_EXPORT_DATA)

    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when the polling options actually changed.

    The coordinator also rewrites the entry data when Tonal rotates the refresh
    token, and that must not trigger a reload — hence the comparison rather than
    an unconditional reload.
    """
    coordinator: TonalCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return

    interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
    fetch_titles = entry.options.get(CONF_FETCH_TITLES, True)

    if (
        coordinator.update_interval == interval
        and coordinator.fetch_titles == fetch_titles
    ):
        return

    await hass.config_entries.async_reload(entry.entry_id)


def _loaded_entries(
    hass: HomeAssistant, *, exclude: str | None = None
) -> list[ConfigEntry]:
    """Return the Tonal entries that currently have a live coordinator."""
    return [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id != exclude and getattr(entry, "runtime_data", None)
    ]


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the export service once, on the first entry to load."""
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_DATA):
        return

    async def _async_export(call: ServiceCall) -> dict[str, str | int]:
        """Write a ToneGet-format export of the current data to disk."""
        entries = _loaded_entries(hass)
        if entry_id := call.data.get("config_entry_id"):
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError("No loaded Tonal config entry to export")

        entry = entries[0]
        coordinator: TonalCoordinator = entry.runtime_data
        if coordinator.data is None:
            raise ServiceValidationError("Tonal has not fetched any data yet")

        use_gzip = call.data[ATTR_GZIP]
        path = call.data.get(ATTR_FILE_PATH)
        if not path:
            stamp = dt_util.now().strftime("%Y%m%d_%H%M%S")
            suffix = ".json.gz" if use_gzip else ".json"
            path = hass.config.path(f"tonal_workouts_{stamp}{suffix}")

        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"{path} is not in allowlist_external_dirs and cannot be written"
            )

        integration = await async_get_integration(hass, DOMAIN)
        payload = build_export(coordinator.data, str(integration.version))

        try:
            size = await hass.async_add_executor_job(
                lambda: write_export(
                    payload, path, use_gzip=use_gzip, full=call.data[ATTR_FULL]
                )
            )
        except OSError as err:
            raise HomeAssistantError(f"Could not write {path}: {err}") from err

        _LOGGER.info("Wrote Tonal export to %s (%s bytes)", path, size)
        return {"file_path": path, "size": size}

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_DATA,
        _async_export,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

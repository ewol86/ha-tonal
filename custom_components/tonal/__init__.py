"""The Tonal integration.

One config entry represents the trainer; each person's Tonal account is an
"account" subentry under it, with its own credentials, coordinator and device.

Unofficial, and unaffiliated with Tonal Systems, Inc.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import TonalApi
from .const import (
    ATTR_ACCOUNT,
    ATTR_FILE_PATH,
    ATTR_FULL,
    ATTR_GZIP,
    CONF_FETCH_TITLES,
    CONF_REFRESH_TOKEN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    SERVICE_EXPORT_DATA,
    SUBENTRY_TYPE_ACCOUNT,
)
from .coordinator import TonalCoordinator
from .export import build_export, write_export

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# subentry_id -> coordinator, one per account on the trainer.
type TonalRuntimeData = dict[str, TonalCoordinator]

EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ACCOUNT): cv.string,
        vol.Optional(ATTR_FILE_PATH): cv.string,
        vol.Optional(ATTR_FULL, default=False): cv.boolean,
        vol.Optional(ATTR_GZIP, default=True): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the trainer and every account configured on it."""
    scan_interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
    fetch_titles = entry.options.get(CONF_FETCH_TITLES, True)

    coordinators: TonalRuntimeData = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ACCOUNT:
            continue

        api = TonalApi(
            async_get_clientsession(hass),
            email=subentry.data[CONF_EMAIL],
            password=subentry.data.get(CONF_PASSWORD),
            refresh_token=subentry.data.get(CONF_REFRESH_TOKEN),
        )
        coordinators[subentry.subentry_id] = TonalCoordinator(
            hass, entry, subentry, api, scan_interval, fetch_titles
        )

    if not coordinators:
        # An entry with no accounts yet is still a valid setup; the user adds
        # accounts from the integration page.
        _LOGGER.debug("No Tonal accounts configured on this entry yet")

    # One account failing must not stop the others from loading.
    await asyncio.gather(
        *(coordinator.async_refresh() for coordinator in coordinators.values())
    )

    if coordinators and not any(
        coordinator.last_update_success for coordinator in coordinators.values()
    ):
        raise ConfigEntryNotReady("No Tonal account could be refreshed")

    for subentry_id, coordinator in coordinators.items():
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "Tonal account %s failed to load; its entities will be "
                "unavailable until the next successful update",
                entry.subentries[subentry_id].title,
            )

    entry.runtime_data = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded and not _loaded_entries(hass, exclude=entry.entry_id):
        hass.services.async_remove(DOMAIN, SERVICE_EXPORT_DATA)

    return unloaded


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the accounts or polling options actually changed.

    Adding, removing or reconfiguring an account fires this, and so does the
    coordinator writing back a rotated refresh token — which must not reload.
    """
    coordinators: TonalRuntimeData | None = getattr(entry, "runtime_data", None)
    if coordinators is None:
        return

    accounts = {
        subentry_id
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_ACCOUNT
    }
    interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
    fetch_titles = entry.options.get(CONF_FETCH_TITLES, True)

    unchanged = (
        accounts == set(coordinators)
        and all(
            coordinator.update_interval == interval
            and coordinator.fetch_titles == fetch_titles
            and coordinator.account_title == entry.subentries[subentry_id].title
            for subentry_id, coordinator in coordinators.items()
        )
    )
    if unchanged:
        return

    await hass.config_entries.async_reload(entry.entry_id)


def _loaded_entries(
    hass: HomeAssistant, *, exclude: str | None = None
) -> list[ConfigEntry]:
    """Return the Tonal entries that currently have live coordinators."""
    return [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id != exclude and getattr(entry, "runtime_data", None)
    ]


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the export service once, on the first entry to load."""
    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_DATA):
        return

    def _resolve(call: ServiceCall) -> tuple[ConfigEntry, TonalCoordinator]:
        """Pick the account to export, or say why it is ambiguous."""
        candidates: list[tuple[ConfigEntry, str, TonalCoordinator]] = [
            (entry, entry.subentries[subentry_id].title, coordinator)
            for entry in _loaded_entries(hass)
            for subentry_id, coordinator in entry.runtime_data.items()
            if subentry_id in entry.subentries
        ]
        if not candidates:
            raise ServiceValidationError("No Tonal account is set up")

        if account := call.data.get(ATTR_ACCOUNT):
            wanted = account.casefold()
            matched = [c for c in candidates if c[1].casefold() == wanted]
            if not matched:
                names = ", ".join(sorted(c[1] for c in candidates))
                raise ServiceValidationError(
                    f"No Tonal account named {account!r}. Available: {names}"
                )
            entry, _, coordinator = matched[0]
            return entry, coordinator

        if len(candidates) > 1:
            names = ", ".join(sorted(c[1] for c in candidates))
            raise ServiceValidationError(
                f"More than one Tonal account is set up; pass 'account'. "
                f"Available: {names}"
            )

        entry, _, coordinator = candidates[0]
        return entry, coordinator

    async def _async_export(call: ServiceCall) -> dict[str, str | int]:
        """Write a ToneGet-format export of one account's data to disk."""
        _, coordinator = _resolve(call)
        if coordinator.data is None:
            raise ServiceValidationError(
                f"Tonal has no data for {coordinator.account_title} yet"
            )

        use_gzip = call.data[ATTR_GZIP]
        path = call.data.get(ATTR_FILE_PATH)
        if not path:
            stamp = dt_util.now().strftime("%Y%m%d_%H%M%S")
            suffix = ".json.gz" if use_gzip else ".json"
            slug = coordinator.account_title.lower().replace(" ", "_")
            path = hass.config.path(f"tonal_{slug}_{stamp}{suffix}")

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

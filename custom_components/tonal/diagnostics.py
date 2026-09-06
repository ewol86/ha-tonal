"""Diagnostics for the Tonal integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import TonalCoordinator

TO_REDACT = {
    "email",
    "password",
    "refresh_token",
    "firstName",
    "lastName",
    "auth0Id",
    "id",
    "userId",
    "birthday",
    "phoneNumber",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The workout list itself is left out; it is large and personal. What is
    included is enough to debug shape and freshness problems.
    """
    coordinator: TonalCoordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "options": dict(entry.options),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "fetch_titles": coordinator.fetch_titles,
        },
        "data": None
        if data is None
        else {
            "user": async_redact_data(data.user, TO_REDACT),
            "profile": async_redact_data(data.profile, TO_REDACT),
            "workout_count": len(data.workouts),
            "custom_workout_count": len(data.custom_workouts),
            "catalog_size": len(data.workout_catalog),
            "regions": data.regions,
            "muscles": data.muscles,
            "strength_history_entries": len(data.strength_history),
            "latest_workout_keys": sorted(data.latest_workout or {}),
        },
    }

"""Data coordinator for the Tonal integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TonalApi, TonalAuthError, TonalError
from .const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    KNOWN_WORKOUT_TYPES,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def parse_time(value: Any) -> datetime | None:
    """Parse one of Tonal's ISO timestamps into an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return dt_util.as_utc(parsed) if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


def workout_duration_minutes(workout: dict[str, Any]) -> float | None:
    """Return a workout's length in minutes, however Tonal happens to report it."""
    for key, divisor in (
        ("duration", 60),
        ("durationSeconds", 60),
        ("totalDuration", 60),
        ("durationMinutes", 1),
    ):
        value = workout.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return round(value / divisor, 1)

    begin = parse_time(workout.get("beginTime"))
    end = parse_time(workout.get("endTime"))
    if begin and end and end > begin:
        return round((end - begin).total_seconds() / 60, 1)

    return None


def _is_custom(workout: dict[str, Any]) -> bool:
    """Return True when a workout activity came from a custom template."""
    workout_type = workout.get("workoutType", "")
    return workout_type == "Custom" or workout_type not in KNOWN_WORKOUT_TYPES


def _parse_strength(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten the current-strength payload into regions and muscles."""
    regions: dict[str, float] = {}
    muscles: dict[str, dict[str, Any]] = {}

    for region in raw:
        if not isinstance(region, dict):
            continue
        region_name = region.get("strengthBodyRegion", "Unknown")
        regions[region_name] = region.get("score", 0)

        for muscle in region.get("familyActivity") or []:
            if not isinstance(muscle, dict):
                continue
            muscles[muscle.get("strengthFamily", "Unknown")] = {
                "score": round(muscle.get("score", 0)),
                "region": region_name,
                "updatedAt": muscle.get("updatedAt"),
            }

    return {"regions": regions, "muscles": muscles}


@dataclass
class TonalData:
    """Everything one refresh pulled back from Tonal."""

    user: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    workouts: list[dict[str, Any]] = field(default_factory=list)
    activity_names: dict[str, str] = field(default_factory=dict)
    workout_catalog: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_workouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    strength_history: list[dict[str, Any]] = field(default_factory=list)
    current_strength: dict[str, Any] = field(default_factory=dict)

    @property
    def latest_workout(self) -> dict[str, Any] | None:
        """Return the most recent workout, if there is one."""
        return self.workouts[0] if self.workouts else None

    @property
    def latest_workout_time(self) -> datetime | None:
        """Return when the most recent workout started."""
        latest = self.latest_workout
        return parse_time(latest.get("beginTime")) if latest else None

    @property
    def regions(self) -> dict[str, Any]:
        """Return the current strength score per body region."""
        parsed = self.current_strength.get("parsed") or {}
        regions = dict(parsed.get("regions") or {})

        # Older accounts only expose the history endpoint; fill the gaps from it.
        if self.strength_history:
            latest = self.strength_history[0]
            for name, key in (
                ("Overall", "overall"),
                ("Upper", "upper"),
                ("Lower", "lower"),
                ("Core", "core"),
            ):
                if regions.get(name) in (None, 0) and latest.get(key) is not None:
                    regions[name] = latest[key]

        return regions

    @property
    def muscles(self) -> dict[str, dict[str, Any]]:
        """Return the current strength score per muscle group."""
        parsed = self.current_strength.get("parsed") or {}
        return dict(parsed.get("muscles") or {})

    def workouts_since(self, days: int) -> int:
        """Count workouts that started within the last ``days`` days."""
        cutoff = dt_util.utcnow() - timedelta(days=days)
        return sum(
            1
            for workout in self.workouts
            if (begin := parse_time(workout.get("beginTime"))) and begin >= cutoff
        )

    def total(self, key: str) -> float:
        """Sum a numeric field across every workout."""
        return sum(
            workout.get(key, 0)
            for workout in self.workouts
            if isinstance(workout.get(key), (int, float))
        )


class TonalCoordinator(DataUpdateCoordinator[TonalData]):
    """Fetches the account's workout history on a schedule."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        api: TonalApi,
        scan_interval: timedelta,
        fetch_titles: bool,
    ) -> None:
        """Initialise the coordinator for one account on the trainer."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {subentry.title}",
            config_entry=entry,
            update_interval=scan_interval,
        )
        # Subentries are frozen and replaced wholesale on update, so hold the
        # id and look the current one up rather than caching the object.
        self.subentry_id = subentry.subentry_id
        self.account_title = subentry.title
        self.api = api
        self.fetch_titles = fetch_titles
        self.user_id: str | None = subentry.unique_id
        # Template titles never change, so they are cached on disk rather than
        # re-fetched (one request each) on every poll.
        self._store: Store[dict[str, str]] = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_TEMPLATE.format(subentry_id=subentry.subentry_id),
        )
        self._titles: dict[str, str] | None = None

    @property
    def subentry(self) -> ConfigSubentry | None:
        """Return the account subentry this coordinator serves."""
        return self.config_entry.subentries.get(self.subentry_id)

    async def _async_load_titles(self) -> dict[str, str]:
        """Load the on-disk template title cache."""
        if self._titles is None:
            self._titles = await self._store.async_load() or {}
        return self._titles

    async def _async_update_titles(self, workouts: list[dict[str, Any]]) -> dict[str, str]:
        """Return template titles, fetching only ids that are not cached yet."""
        titles = await self._async_load_titles()

        if not self.fetch_titles:
            return titles

        unknown = [
            workout_id
            for workout in workouts
            if (workout_id := workout.get("workoutId")) and workout_id not in titles
        ]
        # dict.fromkeys keeps first-seen order while dropping duplicates.
        unknown = list(dict.fromkeys(unknown))

        if not unknown:
            return titles

        _LOGGER.debug("Fetching titles for %s new workout templates", len(unknown))
        fetched = await self.api.async_get_workout_titles(unknown)

        # Record the misses too, so unresolvable ids are not retried forever.
        for workout_id in unknown:
            titles[workout_id] = fetched.get(workout_id, "")

        self._titles = titles
        await self._store.async_save(titles)
        return titles

    def _persist_refresh_token(self) -> None:
        """Write a rotated refresh token back to this account's subentry."""
        token = self.api.refresh_token
        subentry = self.subentry
        if subentry and token and token != subentry.data.get(CONF_REFRESH_TOKEN):
            self.hass.config_entries.async_update_subentry(
                self.config_entry,
                subentry,
                data={**subentry.data, CONF_REFRESH_TOKEN: token},
            )

    async def _async_update_data(self) -> TonalData:
        """Fetch the current state of the account."""
        try:
            user = await self.api.async_get_userinfo()
            self.user_id = user.get("id")
            if not self.user_id:
                raise UpdateFailed("Tonal did not return a user id")

            profile = await self.api.async_get_profile(self.user_id)
            workouts = await self.api.async_get_workouts(self.user_id)
            titles = await self._async_update_titles(workouts)

            today = dt_util.now().strftime("%Y-%m-%d")
            strength_history = await self.api.async_get_strength_history(
                self.user_id, today
            )
            current_raw = await self.api.async_get_current_strength(self.user_id)
        except TonalAuthError as err:
            # Deliberately not ConfigEntryAuthFailed: that would fail the whole
            # config entry and take every other account on the trainer down with
            # it. Subentry flows cannot do reauth, so point at reconfigure.
            _LOGGER.error(
                "Tonal rejected the session for %s (%s). Reconfigure that "
                "account to enter the password again",
                self.account_title,
                err,
            )
            raise UpdateFailed(
                f"Authentication failed for {self.account_title}; "
                "reconfigure the account to re-enter its password"
            ) from err
        except TonalError as err:
            raise UpdateFailed(str(err)) from err

        self._persist_refresh_token()

        # Newest first, matching how the sensors read the list.
        workouts.sort(key=lambda w: w.get("beginTime") or "", reverse=True)

        catalog: dict[str, dict[str, Any]] = {}
        activity_names: dict[str, str] = {}
        custom_workouts: dict[str, dict[str, Any]] = {}

        for workout in workouts:
            template_id = workout.get("workoutId")
            title = titles.get(template_id) if template_id else None
            if title:
                workout["workoutTitle"] = title
                catalog.setdefault(
                    template_id,
                    {
                        "id": template_id,
                        "title": title,
                        "workoutType": workout.get("workoutType"),
                    },
                )
                if _is_custom(workout):
                    custom_workouts[template_id] = catalog[template_id]

            activity_id = workout.get("id") or workout.get("workoutActivityID")
            if activity_id and workout.get("workoutTitle"):
                activity_names[activity_id] = workout["workoutTitle"]

        current_strength: dict[str, Any] = {}
        if current_raw:
            current_strength = {"raw": current_raw, "parsed": _parse_strength(current_raw)}

        return TonalData(
            user=user,
            profile=profile,
            workouts=workouts,
            activity_names=activity_names,
            workout_catalog=catalog,
            custom_workouts=custom_workouts,
            strength_history=strength_history,
            current_strength=current_strength,
        )

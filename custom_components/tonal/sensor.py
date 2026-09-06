"""Sensor platform for the Tonal integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import (
    TonalCoordinator,
    TonalData,
    parse_time,
    workout_duration_minutes,
)

UNIT_POUNDS = "lb"
UNIT_REPS = "reps"
UNIT_WORKOUTS = "workouts"


def _latest(data: TonalData, key: str) -> Any:
    """Return a field from the most recent workout."""
    latest = data.latest_workout
    return latest.get(key) if latest else None


def _days_since_last(data: TonalData) -> float | None:
    """Return whole days since the last workout started."""
    when = data.latest_workout_time
    if when is None:
        return None
    return round((dt_util.utcnow() - when).total_seconds() / 86400, 1)


def _volume_since(data: TonalData, days: int) -> float:
    """Return the volume lifted over the last ``days`` days."""
    cutoff = dt_util.utcnow() - timedelta(days=days)
    total = 0.0
    for workout in data.workouts:
        begin = parse_time(workout.get("beginTime"))
        if begin is None or begin < cutoff:
            continue
        if isinstance(workout.get("totalVolume"), (int, float)):
            total += workout["totalVolume"]
    return round(total)


def _last_workout_attributes(data: TonalData) -> dict[str, Any] | None:
    """Summarise the most recent workout for the attribute panel."""
    latest = data.latest_workout
    if not latest:
        return None

    sets = [s for s in (latest.get("workoutSetActivity") or []) if isinstance(s, dict)]
    heaviest = max(
        (
            s["weight"]
            for s in sets
            if isinstance(s.get("weight"), (int, float))
        ),
        default=None,
    )

    return {
        "workout_type": latest.get("workoutType"),
        "began_at": latest.get("beginTime"),
        "total_volume": latest.get("totalVolume"),
        "total_reps": latest.get("totalReps"),
        "duration_minutes": workout_duration_minutes(latest),
        "set_count": len(sets),
        "heaviest_weight": heaviest,
    }


@dataclass(frozen=True, kw_only=True)
class TonalSensorDescription(SensorEntityDescription):
    """Describes a Tonal sensor."""

    value_fn: Callable[[TonalData], str | float | datetime | None]
    attributes_fn: Callable[[TonalData], dict[str, Any] | None] | None = None


SENSORS: tuple[TonalSensorDescription, ...] = (
    TonalSensorDescription(
        key="total_workouts",
        translation_key="total_workouts",
        icon="mdi:weight-lifter",
        native_unit_of_measurement=UNIT_WORKOUTS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.profile.get("totalWorkouts") or len(data.workouts),
    ),
    TonalSensorDescription(
        key="total_volume",
        translation_key="total_volume",
        icon="mdi:weight-pound",
        native_unit_of_measurement=UNIT_POUNDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda data: data.profile.get("totalVolume")
        or round(data.total("totalVolume")),
    ),
    TonalSensorDescription(
        key="total_reps",
        translation_key="total_reps",
        icon="mdi:counter",
        native_unit_of_measurement=UNIT_REPS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: round(data.total("totalReps")),
    ),
    TonalSensorDescription(
        key="last_workout",
        translation_key="last_workout",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.latest_workout_time,
        attributes_fn=_last_workout_attributes,
    ),
    TonalSensorDescription(
        key="last_workout_title",
        translation_key="last_workout_title",
        icon="mdi:format-title",
        value_fn=lambda data: _latest(data, "workoutTitle")
        or _latest(data, "workoutType"),
        attributes_fn=_last_workout_attributes,
    ),
    TonalSensorDescription(
        key="last_workout_volume",
        translation_key="last_workout_volume",
        icon="mdi:weight-pound",
        native_unit_of_measurement=UNIT_POUNDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: _latest(data, "totalVolume"),
    ),
    TonalSensorDescription(
        key="last_workout_reps",
        translation_key="last_workout_reps",
        icon="mdi:counter",
        native_unit_of_measurement=UNIT_REPS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _latest(data, "totalReps"),
    ),
    TonalSensorDescription(
        key="last_workout_duration",
        translation_key="last_workout_duration",
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: workout_duration_minutes(data.latest_workout or {}),
    ),
    TonalSensorDescription(
        key="days_since_last_workout",
        translation_key="days_since_last_workout",
        icon="mdi:calendar-clock",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_days_since_last,
    ),
    TonalSensorDescription(
        key="workouts_last_7_days",
        translation_key="workouts_last_7_days",
        icon="mdi:calendar-week",
        native_unit_of_measurement=UNIT_WORKOUTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.workouts_since(7),
    ),
    TonalSensorDescription(
        key="workouts_last_30_days",
        translation_key="workouts_last_30_days",
        icon="mdi:calendar-month",
        native_unit_of_measurement=UNIT_WORKOUTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.workouts_since(30),
    ),
    TonalSensorDescription(
        key="volume_last_7_days",
        translation_key="volume_last_7_days",
        icon="mdi:chart-line",
        native_unit_of_measurement=UNIT_POUNDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: _volume_since(data, 7),
    ),
    TonalSensorDescription(
        key="strength_score",
        translation_key="strength_score",
        icon="mdi:arm-flex",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.regions.get("Overall"),
    ),
    TonalSensorDescription(
        key="strength_score_upper",
        translation_key="strength_score_upper",
        icon="mdi:arm-flex",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.regions.get("Upper"),
    ),
    TonalSensorDescription(
        key="strength_score_lower",
        translation_key="strength_score_lower",
        icon="mdi:human-handsdown",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.regions.get("Lower"),
    ),
    TonalSensorDescription(
        key="strength_score_core",
        translation_key="strength_score_core",
        icon="mdi:karate",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.regions.get("Core"),
    ),
    TonalSensorDescription(
        key="custom_workouts",
        translation_key="custom_workouts",
        icon="mdi:playlist-star",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.custom_workouts),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tonal sensors."""
    coordinator: TonalCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        TonalSensor(coordinator, description) for description in SENSORS
    ]

    known_muscles: set[str] = set()

    @callback
    def _async_add_muscles() -> None:
        """Add a sensor for each muscle group Tonal reports."""
        if coordinator.data is None:
            return
        new = sorted(set(coordinator.data.muscles) - known_muscles)
        if not new:
            return
        known_muscles.update(new)
        async_add_entities(TonalMuscleSensor(coordinator, muscle) for muscle in new)

    if coordinator.data is not None:
        known_muscles.update(coordinator.data.muscles)
        entities.extend(
            TonalMuscleSensor(coordinator, muscle)
            for muscle in sorted(known_muscles)
        )

    async_add_entities(entities)

    # Muscle groups only appear once they have been trained, so watch for more.
    entry.async_on_unload(coordinator.async_add_listener(_async_add_muscles))


class TonalEntity(CoordinatorEntity[TonalCoordinator]):
    """Base entity tying everything to one Tonal account device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: TonalCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._account_id = entry.unique_id or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._account_id)},
            manufacturer=MANUFACTURER,
            name=entry.title,
            model="Tonal",
            configuration_url="https://www.tonal.com",
        )


class TonalSensor(TonalEntity, SensorEntity):
    """A sensor built from a static description."""

    entity_description: TonalSensorDescription

    def __init__(
        self, coordinator: TonalCoordinator, description: TonalSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._account_id}_{description.key}"

    @property
    def native_value(self) -> str | float | datetime | None:
        """Return the current value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, when the description supplies any."""
        if self.coordinator.data is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class TonalMuscleSensor(TonalEntity, SensorEntity):
    """Strength score for a single muscle group."""

    _attr_icon = "mdi:arm-flex-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: TonalCoordinator, muscle: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._muscle = muscle
        slug = muscle.lower().replace(" ", "_")
        self._attr_unique_id = f"{self._account_id}_muscle_{slug}"
        self._attr_name = f"{muscle} strength"

    @property
    def native_value(self) -> float | None:
        """Return the muscle's current strength score."""
        if self.coordinator.data is None:
            return None
        return (self.coordinator.data.muscles.get(self._muscle) or {}).get("score")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the body region and when the score last moved."""
        if self.coordinator.data is None:
            return None
        entry = self.coordinator.data.muscles.get(self._muscle) or {}
        return {
            "region": entry.get("region"),
            "updated_at": entry.get("updatedAt"),
        }

    @property
    def available(self) -> bool:
        """Return whether Tonal is still reporting this muscle group."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._muscle in self.coordinator.data.muscles
        )

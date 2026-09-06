"""Building and writing the ToneGet-compatible export file.

The trimming rules and the output schema are kept identical to the upstream
`sync_workouts.py`, so files written here can be fed to anything that already
consumes a ToneGet export.
"""

from __future__ import annotations

import gzip
import json
import os
from typing import Any

from homeassistant.util import dt as dt_util

from .coordinator import TonalData

EXPORT_FORMAT_VERSION = "3.0"

SET_FIELDS_TO_REMOVE = frozenset(
    {
        "beginTimeMCB",
        "endTimeMCB",
        "romWeightMode",
        "romWeight",
        "romWeightFrac",
        "isoModeSpeed",
        "dualMotorReps",
        "suggestedResistanceLevel",
        "offMachineModifiedWeight",
        "userWeightPounds",
        "meanMaxPos",
        "velAtMaxConPower",
        "weightAtMaxConPower",
        "inchesUpdated",
        "powerUpdated",
        "triggeredFeedback",
        "reps",
        "workoutActivityID",
        "workoutId",
        "userId",
        "setId",
    }
)

WORKOUT_FIELDS_TO_REMOVE = frozenset({"deletedAt"})

USER_FIELDS_TO_REMOVE = frozenset(
    {
        "recentMobileDevice",
        "auth0Id",
        "isGuestAccount",
        "isDemoAccount",
        "watchedSafetyVideo",
        "social",
        "profileAssetID",
        "mobileWorkoutsEnabled",
        "accountType",
        "sharingCustomWorkoutsDisabled",
        "workoutDurationMin",
        "workoutDurationMax",
        "updatedPreferencesAt",
        "primaryDeviceType",
        "emailVerified",
        "workoutsPerWeek",
    }
)


def _trim_dict(data: dict[str, Any], drop: frozenset[str]) -> dict[str, Any]:
    """Return a copy of ``data`` without the dropped keys."""
    return {key: value for key, value in data.items() if key not in drop}


def _trim_workout(workout: dict[str, Any]) -> dict[str, Any]:
    """Drop unused fields from a workout and each of its sets."""
    trimmed = _trim_dict(workout, WORKOUT_FIELDS_TO_REMOVE)
    if isinstance(trimmed.get("workoutSetActivity"), list):
        trimmed["workoutSetActivity"] = [
            _trim_dict(entry, SET_FIELDS_TO_REMOVE)
            for entry in trimmed["workoutSetActivity"]
            if isinstance(entry, dict)
        ]
    return trimmed


def trim_export(data: dict[str, Any]) -> dict[str, Any]:
    """Trim an export down to the fields consumers actually use."""
    trimmed = dict(data)

    for key in ("user", "profile"):
        if isinstance(trimmed.get(key), dict):
            trimmed[key] = _trim_dict(trimmed[key], USER_FIELDS_TO_REMOVE)

    if isinstance(trimmed.get("workouts"), list):
        trimmed["workouts"] = [_trim_workout(w) for w in trimmed["workouts"]]

    return trimmed


def build_export(data: TonalData, version: str) -> dict[str, Any]:
    """Assemble the export payload from a coordinator snapshot."""
    return {
        "version": EXPORT_FORMAT_VERSION,
        "exportedAt": dt_util.utcnow().replace(tzinfo=None).isoformat() + "Z",
        "exportedWith": f"Tonal for Home Assistant v{version}",
        "user": data.user,
        "profile": data.profile,
        "workouts": data.workouts,
        "activityNames": data.activity_names,
        "workoutCatalog": data.workout_catalog,
        "customWorkouts": data.custom_workouts,
        "strengthScoreHistory": data.strength_history,
        "currentStrengthScores": data.current_strength,
    }


def write_export(
    payload: dict[str, Any], path: str, *, use_gzip: bool, full: bool
) -> int:
    """Write the export to disk and return the resulting file size in bytes.

    Blocking; call this from the executor.
    """
    if not full:
        payload = trim_export(payload)

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if use_gzip:
        with gzip.open(path, "wb", compresslevel=9) as handle:
            handle.write(raw)
    else:
        with open(path, "wb") as handle:
            handle.write(raw)

    return os.path.getsize(path)

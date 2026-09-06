"""Constants for the Tonal integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "tonal"

# Tonal's public OAuth2 client (the one their mobile app uses).
AUTH0_DOMAIN: Final = "tonal.auth0.com"
CLIENT_ID: Final = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"
API_BASE: Final = "https://api.tonal.com"

# The workout-activities endpoint caps page size at 100.
PAGE_LIMIT: Final = 100

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_FETCH_TITLES: Final = "fetch_titles"
CONF_USER_ID: Final = "user_id"

# One config entry represents the trainer; each account on it is a subentry.
SUBENTRY_TYPE_ACCOUNT: Final = "account"

DEFAULT_SCAN_INTERVAL_MINUTES: Final = 30
MIN_SCAN_INTERVAL_MINUTES: Final = 5
MAX_SCAN_INTERVAL_MINUTES: Final = 1440

# Workout types Tonal ships itself; anything else came from a custom template.
KNOWN_WORKOUT_TYPES: Final = (
    "PROGRAM",
    "ON_DEMAND",
    "QUICK_FIT",
    "LIVE",
    "MOVEMENT",
    "ASSESSMENT",
)

ATTRIBUTION: Final = "Data provided by Tonal"
MANUFACTURER: Final = "Tonal"

SERVICE_EXPORT_DATA: Final = "export_data"
ATTR_ACCOUNT: Final = "account"
ATTR_FILE_PATH: Final = "file_path"
ATTR_FULL: Final = "full"
ATTR_GZIP: Final = "gzip"

STORAGE_VERSION: Final = 1
STORAGE_KEY_TEMPLATE: Final = "tonal.titles.{subentry_id}"

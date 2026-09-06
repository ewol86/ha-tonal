"""Async client for Tonal's private API.

This is a port of the request flow used by the `toneget` CLI
(https://github.com/curlrequests/toneget) onto aiohttp, plus refresh-token
handling so the integration can stay logged in without replaying the password
on every poll.

Unofficial, and unaffiliated with Tonal Systems, Inc.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_BASE, AUTH0_DOMAIN, CLIENT_ID, PAGE_LIMIT

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = ClientTimeout(total=30)
# Renew a little before the token actually dies, so a long refresh cannot
# straddle the expiry boundary.
TOKEN_EXPIRY_MARGIN = 300
# Be gentle when walking the workout catalog; on a first run that is one
# request per distinct template.
TEMPLATE_REQUEST_DELAY = 0.05


class TonalError(Exception):
    """Base error for the Tonal API."""


class TonalAuthError(TonalError):
    """Credentials were rejected, or the session can no longer be renewed."""


class TonalConnectionError(TonalError):
    """Tonal could not be reached."""


class TonalApiError(TonalError):
    """Tonal answered, but not with anything usable."""


class TonalApi:
    """Talks to Tonal's Auth0 tenant and REST API for a single account."""

    def __init__(
        self,
        session: ClientSession,
        *,
        email: str | None = None,
        password: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Initialise the client with whichever credentials are available."""
        self._session = session
        self._email = email
        self._password = password
        self._refresh_token = refresh_token
        self._id_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str | None:
        """Return the current refresh token, if Tonal issued one."""
        return self._refresh_token

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _token_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post to the Auth0 token endpoint and normalise the failure modes."""
        try:
            response = await self._session.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            raise TonalConnectionError("Timed out talking to Tonal") from err
        except ClientError as err:
            raise TonalConnectionError(f"Failed to connect to Tonal: {err}") from err

        if response.status in (401, 403):
            raise TonalAuthError("Tonal rejected the credentials")
        if response.status != 200:
            body = await response.text()
            raise TonalApiError(f"Authentication failed: {response.status} - {body}")

        try:
            return await response.json(content_type=None)
        except ValueError as err:
            raise TonalApiError("Tonal returned a malformed token response") from err

    def _store_tokens(self, data: dict[str, Any]) -> None:
        """Remember the tokens from a successful grant."""
        id_token = data.get("id_token")
        if not id_token:
            raise TonalApiError("Tonal did not return an id_token")

        self._id_token = id_token
        # Auth0 reports seconds; fall back to an hour if it ever stops doing so.
        self._expires_at = time.monotonic() + float(data.get("expires_in") or 3600)
        if refresh_token := data.get("refresh_token"):
            self._refresh_token = refresh_token

    async def async_login(self) -> None:
        """Authenticate with email and password (resource owner password grant)."""
        if not self._email or not self._password:
            raise TonalAuthError("No password available to log in with")

        data = await self._token_request(
            {
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": self._email,
                "password": self._password,
                "scope": "openid profile email offline_access",
            }
        )
        self._store_tokens(data)

    async def _async_renew(self) -> None:
        """Renew the session, preferring the refresh token over the password."""
        if self._refresh_token:
            try:
                data = await self._token_request(
                    {
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": self._refresh_token,
                    }
                )
            except TonalAuthError:
                _LOGGER.debug("Refresh token rejected, falling back to password")
                self._refresh_token = None
            else:
                self._store_tokens(data)
                return

        if not self._password:
            raise TonalAuthError("Session expired and no password is stored")
        await self.async_login()

    async def async_token(self) -> str:
        """Return a valid id_token, renewing first if it is close to expiry."""
        async with self._lock:
            if (
                self._id_token
                and time.monotonic() < self._expires_at - TOKEN_EXPIRY_MARGIN
            ):
                return self._id_token
            await self._async_renew()
            if self._id_token is None:
                raise TonalAuthError("Could not obtain a Tonal session token")
            return self._id_token

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        allow_missing: bool = False,
    ) -> tuple[Any, dict[str, str]]:
        """GET a path under the API base, returning (json, response headers)."""
        token = await self.async_token()
        headers = {"Authorization": f"Bearer {token}"}
        if extra_headers:
            headers.update(extra_headers)

        try:
            response = await self._session.get(
                f"{API_BASE}{path}",
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            raise TonalConnectionError(f"Timed out fetching {path}") from err
        except ClientError as err:
            raise TonalConnectionError(f"Failed to fetch {path}: {err}") from err

        if response.status in (401, 403):
            raise TonalAuthError(f"Tonal denied access to {path}")
        if response.status != 200:
            if allow_missing:
                _LOGGER.debug("Ignoring %s from %s", response.status, path)
                return None, dict(response.headers)
            raise TonalApiError(f"Unexpected {response.status} from {path}")

        try:
            payload = await response.json(content_type=None)
        except ValueError as err:
            raise TonalApiError(f"Malformed JSON from {path}") from err

        return payload, dict(response.headers)

    async def async_get_userinfo(self) -> dict[str, Any]:
        """Return the account record, including the user id everything else needs."""
        data, _ = await self._get("/v6/users/userinfo")
        if not isinstance(data, dict):
            raise TonalApiError("Unexpected userinfo response")
        return data

    async def async_get_profile(self, user_id: str) -> dict[str, Any]:
        """Return lifetime profile stats; absent on some accounts."""
        data, _ = await self._get(f"/v6/users/{user_id}/profile", allow_missing=True)
        return data if isinstance(data, dict) else {}

    async def async_get_workouts(self, user_id: str) -> list[dict[str, Any]]:
        """Return every workout activity, walking Tonal's header-based pagination."""
        path = f"/v6/users/{user_id}/workout-activities"
        first_page, headers = await self._get(
            path, extra_headers={"pg-offset": "0", "pg-limit": str(PAGE_LIMIT)}
        )

        try:
            total = int(headers.get("pg-total", 0))
        except (TypeError, ValueError):
            total = 0

        workouts: list[dict[str, Any]] = list(first_page or [])
        offset = PAGE_LIMIT

        while offset < total:
            page, _ = await self._get(
                path,
                extra_headers={"pg-offset": str(offset), "pg-limit": str(PAGE_LIMIT)},
                allow_missing=True,
            )
            if page:
                workouts.extend(page)
            else:
                _LOGGER.debug("Empty workout page at offset %s", offset)
            offset += PAGE_LIMIT

        return workouts

    async def async_get_workout_template(
        self, workout_id: str
    ) -> dict[str, Any] | None:
        """Return one workout template, or None when it is not retrievable."""
        data, _ = await self._get(f"/v6/workouts/{workout_id}", allow_missing=True)
        return data if isinstance(data, dict) else None

    async def async_get_workout_titles(self, workout_ids: list[str]) -> dict[str, str]:
        """Return {template id: title} for the given templates.

        Only ids and titles are kept, so none of Tonal's program structure or
        instructional content is stored.
        """
        titles: dict[str, str] = {}
        for workout_id in workout_ids:
            try:
                details = await self.async_get_workout_template(workout_id)
            except TonalConnectionError:
                # One flaky template lookup should not sink the whole refresh.
                _LOGGER.debug("Could not fetch template %s", workout_id)
                continue

            if not details:
                continue

            title = (
                details.get("title")
                or details.get("name")
                or details.get("displayName")
                or details.get("workoutTitle")
            )
            if title:
                titles[workout_id] = title

            await asyncio.sleep(TEMPLATE_REQUEST_DELAY)

        return titles

    async def async_get_strength_history(
        self, user_id: str, end_date: str
    ) -> list[dict[str, Any]]:
        """Return the strength score history, newest first."""
        data, _ = await self._get(
            f"/v6/users/{user_id}/strength-scores/history",
            params={"limit": 5000, "endDate": end_date},
            allow_missing=True,
        )
        return data if isinstance(data, list) else []

    async def async_get_current_strength(self, user_id: str) -> list[dict[str, Any]]:
        """Return the current per-region and per-muscle strength breakdown."""
        data, _ = await self._get(
            f"/v6/users/{user_id}/strength-scores/current", allow_missing=True
        )
        return data if isinstance(data, list) else []

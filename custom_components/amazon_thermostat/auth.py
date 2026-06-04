"""Authentication providers for Amazon Thermostat."""

from __future__ import annotations

from typing import Any, Protocol

from aiohttp import ClientSession

from .const import (
    AUTH_METHOD_COOKIE2,
    CONF_AMAZON_DOMAIN,
    CONF_AUTH_METHOD,
    CONF_COOKIE_DATA,
    DEFAULT_AMAZON_DOMAIN,
    USER_AGENT,
)
from .cookie2 import refresh_cookie2_login
from .models import AmazonThermostatAuthError


class AlexaAuthSessionProvider(Protocol):
    """Provides an authenticated session for Alexa GraphQL requests."""

    amazon_domain: str

    async def async_get_session(self) -> ClientSession:
        """Return the aiohttp session that owns the auth cookies."""

    async def async_get_headers(self) -> dict[str, str]:
        """Return headers for an Alexa GraphQL request."""

    async def async_refresh(self) -> bool:
        """Refresh credentials if possible."""

    async def async_export_entry_data(self) -> dict[str, Any]:
        """Return config-entry data needed to resume this auth session."""


class Cookie2AuthSessionProvider:
    """Auth provider backed by alexa-cookie2-compatible cookie data."""

    def __init__(
        self,
        session: ClientSession,
        amazon_domain: str,
        cookie_data: dict[str, Any],
    ) -> None:
        """Initialize the cookie2 provider."""
        self._session = session
        self.amazon_domain = amazon_domain
        self._cookie_data = cookie_data

    async def async_get_session(self) -> ClientSession:
        """Return the Home Assistant aiohttp session."""
        return self._session

    async def async_get_headers(self) -> dict[str, str]:
        """Return cookie2-authenticated request headers."""
        base_url = f"https://alexa.{self.amazon_domain}"
        csrf = self._cookie_data.get("csrf")
        cookie = self._cookie_data.get("localCookie")
        if not csrf or not cookie:
            raise AmazonThermostatAuthError("Cookie2 auth data is incomplete")
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "Accept-Language": "en-US",
            "Referer": f"{base_url}/spa/index.html",
            "Origin": base_url,
            "csrf": csrf,
            "Cookie": cookie,
        }

    async def async_refresh(self) -> bool:
        """Refresh cookie2 auth data."""
        try:
            self._cookie_data = await refresh_cookie2_login(
                self._session, self._cookie_data, self.amazon_domain
            )
        except AmazonThermostatAuthError:
            return False
        return True

    async def async_export_entry_data(self) -> dict[str, Any]:
        """Export entry data."""
        return {
            CONF_AUTH_METHOD: AUTH_METHOD_COOKIE2,
            CONF_AMAZON_DOMAIN: self.amazon_domain,
            CONF_COOKIE_DATA: self._cookie_data,
        }


def build_auth_provider_from_entry(
    session: ClientSession,
    entry_data: dict[str, Any],
) -> AlexaAuthSessionProvider:
    """Build the cookie2 auth provider for a config entry."""
    amazon_domain = entry_data.get(CONF_AMAZON_DOMAIN, DEFAULT_AMAZON_DOMAIN)
    return Cookie2AuthSessionProvider(
        session,
        amazon_domain,
        entry_data[CONF_COOKIE_DATA],
    )

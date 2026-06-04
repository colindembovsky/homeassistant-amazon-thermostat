"""Authentication providers for Amazon Thermostat."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any, Protocol

from aiohttp import ClientSession

from .const import (
    AUTH_METHOD_ALEXAPY,
    AUTH_METHOD_MANUAL,
    CONF_AMAZON_DOMAIN,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_CSRF,
    CONF_EMAIL,
    CONF_OAUTH,
    CONF_OTPSECRET,
    CONF_PASSWORD,
    DEFAULT_AMAZON_DOMAIN,
    USER_AGENT,
)
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


class ManualCookieAuthSessionProvider:
    """Auth provider for manually supplied cookie and CSRF values."""

    def __init__(
        self,
        session: ClientSession,
        amazon_domain: str,
        cookie: str,
        csrf: str,
    ) -> None:
        """Initialize the manual provider."""
        self._session = session
        self.amazon_domain = amazon_domain
        self._cookie = cookie
        self._csrf = csrf

    async def async_get_session(self) -> ClientSession:
        """Return the Home Assistant aiohttp session."""
        return self._session

    async def async_get_headers(self) -> dict[str, str]:
        """Return cookie-authenticated request headers."""
        base_url = f"https://alexa.{self.amazon_domain}"
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "Accept-Language": "en-US",
            "Referer": f"{base_url}/spa/index.html",
            "Origin": base_url,
            "csrf": self._csrf,
            "Cookie": self._cookie,
        }

    async def async_refresh(self) -> bool:
        """Manual cookie auth cannot refresh itself."""
        return False

    async def async_export_entry_data(self) -> dict[str, Any]:
        """Export entry data."""
        return {
            CONF_AUTH_METHOD: AUTH_METHOD_MANUAL,
            CONF_AMAZON_DOMAIN: self.amazon_domain,
            CONF_COOKIE: self._cookie,
            CONF_CSRF: self._csrf,
        }


class AlexaPyAuthSessionProvider:
    """Auth provider backed by alexapy's AlexaLogin session."""

    def __init__(self, login: Any) -> None:
        """Initialize the alexapy provider."""
        self.login = login
        self.amazon_domain = login.url

    async def async_get_session(self) -> ClientSession:
        """Return alexapy's aiohttp session."""
        if not self.login.session or self.login.session.closed:
            raise AmazonThermostatAuthError("Alexa login session is closed")
        return self.login.session

    async def async_get_headers(self) -> dict[str, str]:
        """Return alexapy-authenticated request headers."""
        await self._ensure_fresh()
        base_url = f"https://alexa.{self.amazon_domain}"
        headers = dict(getattr(self.login, "_headers", {}) or {})
        headers.update(
            {
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json; charset=utf-8",
                "Referer": f"{base_url}/spa/index.html",
                "Origin": base_url,
            }
        )
        csrf = self._csrf_token()
        if csrf:
            headers["csrf"] = csrf
        return headers

    async def async_refresh(self) -> bool:
        """Refresh OAuth/cookies through alexapy when possible."""
        return await self._refresh_alexapy()

    async def async_export_entry_data(self) -> dict[str, Any]:
        """Export resumable alexapy auth data."""
        return {
            CONF_AUTH_METHOD: AUTH_METHOD_ALEXAPY,
            CONF_AMAZON_DOMAIN: self.amazon_domain,
            CONF_EMAIL: self.login.email,
            CONF_PASSWORD: self.login.password,
            CONF_OTPSECRET: "",
            CONF_OAUTH: _oauth_data_from_login(self.login),
        }

    async def _ensure_fresh(self) -> None:
        """Refresh expired alexapy tokens before requests."""
        expires_in = getattr(self.login, "expires_in", None)
        if expires_in and expires_in - time.time() < 0:
            if not await self._refresh_alexapy():
                raise AmazonThermostatAuthError("Unable to refresh Alexa auth")

    async def _refresh_alexapy(self) -> bool:
        """Run alexapy's token/cookie refresh sequence."""
        refresh = getattr(self.login, "refresh_access_token", None)
        exchange = getattr(self.login, "exchange_token_for_cookies", None)
        get_csrf = getattr(self.login, "get_csrf", None)
        finalize = getattr(self.login, "finalize_login", None)
        if not all((refresh, exchange, get_csrf, finalize)):
            return False
        if await refresh() and await exchange() and await get_csrf():
            await finalize()
            return True
        return False

    def _csrf_token(self) -> str | None:
        """Extract csrf from alexapy session/header state."""
        if csrf := getattr(self.login, "csrf_token", None):
            return csrf
        headers = getattr(self.login, "_headers", {}) or {}
        if csrf := headers.get("csrf"):
            return csrf
        try:
            cookies = self.login._get_cookies_from_session(f"alexa.{self.amazon_domain}")
            csrf_cookie = cookies.get("csrf")
            return getattr(csrf_cookie, "value", csrf_cookie)
        except (AttributeError, KeyError):
            return None


def build_auth_provider_from_entry(
    session: ClientSession,
    entry_data: dict[str, Any],
    output_path: Callable[[str], str],
) -> AlexaAuthSessionProvider:
    """Build the configured auth provider for a config entry."""
    auth_method = entry_data.get(CONF_AUTH_METHOD, AUTH_METHOD_MANUAL)
    amazon_domain = entry_data.get(CONF_AMAZON_DOMAIN, DEFAULT_AMAZON_DOMAIN)
    if auth_method == AUTH_METHOD_ALEXAPY:
        return AlexaPyAuthSessionProvider(
            create_alexapy_login(amazon_domain, entry_data, output_path)
        )
    return ManualCookieAuthSessionProvider(
        session,
        amazon_domain,
        entry_data[CONF_COOKIE],
        entry_data[CONF_CSRF],
    )


def create_alexapy_login(
    amazon_domain: str,
    data: dict[str, Any],
    output_path: Callable[[str], str],
) -> Any:
    """Create an AlexaLogin object lazily so tests do not require alexapy."""
    try:
        from alexapy import AlexaLogin
    except ImportError as err:
        raise AmazonThermostatAuthError("alexapy is not installed") from err

    return AlexaLogin(
        url=amazon_domain,
        email=data.get(CONF_EMAIL, ""),
        password=data.get(CONF_PASSWORD, ""),
        outputpath=output_path,
        otp_secret=data.get(CONF_OTPSECRET, ""),
        oauth=data.get(CONF_OAUTH, {}),
        oauth_login=True,
    )


def _oauth_data_from_login(login: Any) -> dict[str, Any]:
    """Extract the alexapy OAuth fields needed for resume/refresh."""
    return {
        "access_token": getattr(login, "access_token", None),
        "refresh_token": getattr(login, "refresh_token", None),
        "expires_in": getattr(login, "expires_in", None),
        "mac_dms": getattr(login, "mac_dms", None),
        "code_verifier": getattr(login, "code_verifier", None),
        "authorization_code": getattr(login, "authorization_code", None),
    }


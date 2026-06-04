"""Tests for the Alexa GraphQL API client."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.amazon_thermostat.api import AlexaThermostatApi
from custom_components.amazon_thermostat.auth import Cookie2AuthSessionProvider
from custom_components.amazon_thermostat.cookie2 import Cookie2LoginProxy, Cookie2State
from custom_components.amazon_thermostat.models import (
    AmazonThermostatApiError,
    AmazonThermostatAuthError,
    AmazonThermostatThrottleError,
)


class FakeResponse:
    """Minimal aiohttp response double."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    def raise_for_status(self) -> None:
        """No-op for successful fake responses."""
        if self.status >= 400:
            from aiohttp import ClientResponseError, RequestInfo
            from yarl import URL

            request_info = RequestInfo(URL("https://example.com"), "POST", {}, URL("https://example.com"))
            raise ClientResponseError(request_info, (), status=self.status)

    async def json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return fake JSON."""
        return self._payload


class FakeSession:
    """Minimal aiohttp session double."""

    def __init__(
        self,
        status: int | list[int] = 200,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.statuses = status if isinstance(status, list) else [status]
        self.payload = payload or {"data": {"setEndpointFeatures": {"errors": []}}}
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Capture and return a fake response."""
        self.requests.append({"url": url, **kwargs})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return FakeResponse(status, self.payload)


def _api(session: FakeSession) -> AlexaThermostatApi:
    """Build an API client with the cookie2 auth provider."""
    return AlexaThermostatApi(
        Cookie2AuthSessionProvider(  # type: ignore[arg-type]
            session,
            "amazon.com",
            {"localCookie": "cookie=value", "csrf": "csrf-token"},
        )
    )


class RefreshableProvider(Cookie2AuthSessionProvider):
    """Cookie2 provider double that can refresh once."""

    def __init__(self, session: FakeSession) -> None:
        super().__init__(  # type: ignore[arg-type]
            session,
            "amazon.com",
            {"localCookie": "cookie=value", "csrf": "csrf-token"},
        )
        self.refreshes = 0

    async def async_refresh(self) -> bool:
        """Pretend refresh succeeds once."""
        self.refreshes += 1
        return True


@pytest.mark.asyncio
async def test_set_temperature_range_sends_dual_setpoint_payload() -> None:
    """Range writes send lower and upper setpoints in a single mutation."""
    session = FakeSession()
    api = _api(session)

    await api.async_set_temperature_range(
        "amzn1.alexa.endpoint.thermostat", 68, 76, "FAHRENHEIT"
    )

    request = session.requests[0]
    feature_request = request["json"]["variables"]["featureControlRequests"][0]
    assert request["url"] == "https://alexa.amazon.com/nexus/v1/graphql"
    assert feature_request["featureName"] == "thermostat"
    assert feature_request["featureOperationName"] == "setTargetSetpoint"
    assert feature_request["payload"] == {
        "lowerSetpoint": {"value": "68", "scale": "FAHRENHEIT"},
        "upperSetpoint": {"value": "76", "scale": "FAHRENHEIT"},
    }
    assert request["headers"]["csrf"] == "csrf-token"
    assert request["headers"]["Cookie"] == "cookie=value"


@pytest.mark.asyncio
async def test_set_target_temperature_sends_single_setpoint_payload() -> None:
    """Single target writes send targetSetpoint."""
    session = FakeSession()
    api = _api(session)

    await api.async_set_target_temperature(
        "amzn1.alexa.endpoint.thermostat", 72, "FAHRENHEIT"
    )

    feature_request = session.requests[0]["json"]["variables"]["featureControlRequests"][0]
    assert feature_request["payload"] == {
        "targetSetpoint": {"value": "72", "scale": "FAHRENHEIT"}
    }


@pytest.mark.asyncio
async def test_mutation_errors_raise_api_error() -> None:
    """GraphQL mutation errors are surfaced."""
    session = FakeSession(
        payload={
            "data": {
                "setEndpointFeatures": {
                    "errors": [{"endpointId": "id", "code": "ENDPOINT_UNREACHABLE"}]
                }
            }
        }
    )
    api = _api(session)

    with pytest.raises(AmazonThermostatApiError):
        await api.async_turn_off("amzn1.alexa.endpoint.thermostat")


@pytest.mark.asyncio
async def test_auth_status_raises_auth_error() -> None:
    """401/403 responses become auth errors."""
    session = FakeSession(status=401)
    api = _api(session)

    with pytest.raises(AmazonThermostatAuthError):
        await api.async_turn_off("amzn1.alexa.endpoint.thermostat")


@pytest.mark.asyncio
async def test_rate_limit_status_raises_throttle_error() -> None:
    """429 responses become throttle errors."""
    session = FakeSession(status=429)
    api = _api(session)

    with pytest.raises(AmazonThermostatThrottleError):
        await api.async_turn_off("amzn1.alexa.endpoint.thermostat")


@pytest.mark.asyncio
async def test_auth_error_refreshes_and_retries_once() -> None:
    """A 401 triggers provider refresh and retries the GraphQL request."""
    session = FakeSession(status=[401, 200])
    provider = RefreshableProvider(session)
    api = AlexaThermostatApi(provider)

    await api.async_turn_off("amzn1.alexa.endpoint.thermostat")

    assert provider.refreshes == 1
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_cookie2_provider_uses_local_cookie_and_csrf() -> None:
    """Cookie2 provider builds GraphQL headers from localCookie data."""
    session = FakeSession()
    provider = Cookie2AuthSessionProvider(  # type: ignore[arg-type]
        session,
        "amazon.com",
        {"localCookie": "cookie=value", "csrf": "csrf-token", "refreshToken": "refresh"},
    )

    headers = await provider.async_get_headers()
    entry_data = await provider.async_export_entry_data()

    assert headers["Cookie"] == "cookie=value"
    assert headers["csrf"] == "csrf-token"
    assert entry_data["auth_method"] == "cookie2"
    assert entry_data["cookie_data"]["refreshToken"] == "refresh"


def test_cookie2_state_builds_homebridge_style_oauth_url() -> None:
    """Cookie2 login starts with the alexa-cookie2 device OAuth URL."""
    state = Cookie2State(
        amazon_domain="amazon.com",
        proxy_base_url="http://homeassistant.local:8124",
        callback_url="http://homeassistant.local:8123/auth/amazon_thermostat/callback",
        flow_id="flow",
    )

    assert state.initial_url.startswith("https://www.amazon.com/ap/signin?")
    assert "openid.oa2.response_type=code" in state.initial_url
    assert "openid.oa2.scope=device_auth_access" in state.initial_url
    assert "openid.oa2.code_challenge=" in state.initial_url


def test_cookie2_proxy_rewrites_absolute_amazon_urls_in_body() -> None:
    """Root proxy rewrites absolute Amazon URLs to proxy URLs."""
    state = Cookie2State(
        amazon_domain="amazon.com",
        proxy_base_url="http://homeassistant.local:8124",
        callback_url="http://homeassistant.local:8123/auth/amazon_thermostat/callback",
        flow_id="flow",
    )
    proxy = Cookie2LoginProxy(None, state)  # type: ignore[arg-type]

    body = (
        b'<a href="https://www.amazon.com/ap/signin">In</a>'
        b'<img src="https://alexa.amazon.com/spa/x.png">'
    )

    rewritten = proxy._rewrite_body(body).decode()

    assert 'href="http://homeassistant.local:8124/www.amazon.com/ap/signin"' in rewritten
    assert 'src="http://homeassistant.local:8124/alexa.amazon.com/spa/x.png"' in rewritten


def test_cookie2_proxy_routes_root_relative_via_referer() -> None:
    """Root-relative CVF requests route to Amazon using the Referer host."""
    state = Cookie2State(
        amazon_domain="amazon.com",
        proxy_base_url="http://homeassistant.local:8124",
        callback_url="http://homeassistant.local:8123/auth/amazon_thermostat/callback",
        flow_id="flow",
    )
    proxy = Cookie2LoginProxy(None, state)  # type: ignore[arg-type]

    class _FakeRequest:
        def __init__(self, tail: str, query: str, referer: str) -> None:
            self.match_info = {"tail": tail}
            self.query_string = query
            self.headers = {"Referer": referer} if referer else {}

    # Root-relative AJAX during CVF, referred to by a www page.
    www_req = _FakeRequest(
        "ap/cvf/verify",
        "arb=123",
        "http://homeassistant.local:8124/www.amazon.com/ap/cvf/request",
    )
    assert (
        str(proxy._target_url(www_req))  # type: ignore[arg-type]
        == "https://www.amazon.com/ap/cvf/verify?arb=123"
    )

    # Host-prefixed path routes directly.
    prefixed = _FakeRequest("www.amazon.com/ap/signin", "", "")
    assert (
        str(proxy._target_url(prefixed))  # type: ignore[arg-type]
        == "https://www.amazon.com/ap/signin"
    )

    # Referer pointing at the alexa host routes there.
    alexa_req = _FakeRequest(
        "api/devices",
        "",
        "http://homeassistant.local:8124/alexa.amazon.com/spa/index.html",
    )
    assert (
        str(proxy._target_url(alexa_req))  # type: ignore[arg-type]
        == "https://alexa.amazon.com/api/devices"
    )


def test_cookie2_proxy_rewrites_set_cookie_for_browser_cvf() -> None:
    """Amazon cookies are forwarded as host cookies for browser-side CVF pages."""
    state = Cookie2State(
        amazon_domain="amazon.com",
        proxy_base_url="http://homeassistant.local:8124",
        callback_url="http://homeassistant.local:8123/auth/amazon_thermostat/callback",
        flow_id="flow",
    )
    proxy = Cookie2LoginProxy(None, state)  # type: ignore[arg-type]

    cookie = proxy._rewrite_set_cookie(
        "session-id=abc; Domain=.amazon.com; Path=/ap; Secure; HttpOnly"
    )

    assert cookie == "session-id=abc; Path=/; HttpOnly"

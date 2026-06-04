"""Python port of the alexa-cookie2 proxy/token flow used by Homebridge."""

from __future__ import annotations

from base64 import b64encode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

from aiohttp import ClientSession, web
from yarl import URL

from .const import USER_AGENT
from .models import AmazonThermostatAuthError, AmazonThermostatApiError

API_CALL_VERSION = "2.2.556530.0"
API_CALL_USER_AGENT = "AmazonWebView/Amazon Alexa/2.2.556530.0/iOS/16.6/iPhone"
DEFAULT_ACCEPT_LANGUAGE = "en-US"
DEFAULT_PROXY_LANGUAGE = "en_US"
DEFAULT_DEVICE_APP_NAME = "Home Assistant"
DEVICE_TYPE = "A2IVLV5VM2W81"
DEVICE_ID_SUFFIX = "23413249564c5635564d32573831"

CSRF_PATHS = (
    "/api/language",
    "/spa/index.html",
    "/api/devices-v2/device?cached=false",
    "/templates/oobe/d-device-pick.handlebars",
    "/api/strings",
)


@dataclass
class Cookie2State:
    """State shared by the login proxy and token exchange."""

    amazon_domain: str
    proxy_base_url: str
    callback_url: str
    flow_id: str
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE
    proxy_language: str = DEFAULT_PROXY_LANGUAGE
    device_app_name: str = DEFAULT_DEVICE_APP_NAME
    frc: str = ""
    map_md: str = ""
    device_id: str = ""
    code_verifier: str = ""
    code_challenge: str = ""
    proxy_cookie: str = ""
    authorization_code: str | None = None

    def __post_init__(self) -> None:
        """Populate generated values in the same shape as alexa-cookie2."""
        self.frc = self.frc or b64encode(secrets.token_bytes(313)).decode()
        self.map_md = self.map_md or b64encode(
            json.dumps(
                {
                    "device_user_dictionary": [],
                    "device_registration_data": {"software_version": "1"},
                    "app_identifier": {
                        "app_version": "2.2.485407",
                        "bundle_id": "com.amazon.echo",
                    },
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
        if not self.device_id:
            serial_hex = secrets.token_hex(16).upper()
            self.device_id = serial_hex.encode().hex() + DEVICE_ID_SUFFIX
        if not self.code_verifier:
            self.code_verifier = _base64url(secrets.token_bytes(32))
        if not self.code_challenge:
            self.code_challenge = _base64url(sha256(self.code_verifier.encode()).digest())

    @property
    def initial_url(self) -> str:
        """Return the alexa-cookie2 initial OAuth sign-in URL."""
        handle = "_jp" if self.amazon_domain.endswith(".jp") else ""
        params = {
            "openid.return_to": f"https://www.{self.amazon_domain}/ap/maplanding",
            "openid.assoc_handle": f"amzn_dp_project_dee_ios{handle}",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "pageId": f"amzn_dp_project_dee_ios{handle}",
            "accountStatusPolicy": "P1",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.mode": "checkid_setup",
            "openid.ns.oa2": f"http://www.{self.amazon_domain}/ap/ext/oauth/2",
            "openid.oa2.client_id": f"device:{self.device_id}",
            "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
            "openid.oa2.response_type": "code",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.pape.max_auth_age": "0",
            "openid.oa2.scope": "device_auth_access",
            "openid.oa2.code_challenge_method": "S256",
            "openid.oa2.code_challenge": self.code_challenge,
            "language": self.proxy_language,
        }
        return f"https://www.{self.amazon_domain}/ap/signin?{urlencode(params)}"


class Cookie2LoginProxy:
    """Small reverse proxy compatible with alexa-cookie2's login flow."""

    def __init__(self, session: ClientSession, state: Cookie2State) -> None:
        """Initialize the proxy."""
        self._session = session
        self.state = state

    async def handle(self, request: web.Request, **_: Any) -> web.StreamResponse:
        """Proxy one Amazon login request."""
        target_url = self._target_url(request)
        headers = self._request_headers(request, target_url)
        body = await request.read() if request.method in {"POST", "PUT", "PATCH"} else None

        async with self._session.request(
            request.method,
            target_url,
            headers=headers,
            data=body,
            allow_redirects=False,
        ) as response:
            self._capture_set_cookies(response.headers.getall("Set-Cookie", []))
            location = response.headers.get("Location")
            if location and self._is_success_location(location):
                self._capture_success(location)
                raise web.HTTPFound(self.state.callback_url)
            if location:
                raise web.HTTPFound(self._rewrite_to_proxy(location, target_url.host))

            content = await response.read()
            outgoing_headers = self._response_headers(response.headers)
            content_type = response.headers.get("Content-Type", "")
            if b"text/html" in content_type.encode() or b"text/javascript" in content_type.encode():
                content = self._rewrite_body(content)
            return web.Response(
                status=response.status,
                body=content,
                headers=outgoing_headers,
            )

    def _target_url(self, request: web.Request) -> URL:
        """Map a proxy request to an Amazon URL."""
        tail = request.match_info.get("tail", "")
        if not tail:
            return URL(self.state.initial_url)
        if tail.startswith(f"www.{self.state.amazon_domain}/"):
            rest = tail.removeprefix(f"www.{self.state.amazon_domain}")
            return URL(f"https://www.{self.state.amazon_domain}{rest}").with_query(
                request.query
            )
        if tail.startswith(f"alexa.{self.state.amazon_domain}/"):
            rest = tail.removeprefix(f"alexa.{self.state.amazon_domain}")
            return URL(f"https://alexa.{self.state.amazon_domain}{rest}").with_query(
                request.query
            )
        return URL(f"https://www.{self.state.amazon_domain}/{tail}").with_query(
            request.query
        )

    def _request_headers(self, request: web.Request, target_url: URL) -> dict[str, str]:
        """Build upstream request headers."""
        headers = {
            "User-Agent": "AppleWebKit PitanguiBridge/2.2.485407.0-[HARDWARE=iPhone10_4][SOFTWARE=15.5][DEVICE=iPhone]",
            "Accept-Language": self.state.accept_language,
            "Accept": request.headers.get("Accept", "*/*"),
            "Connection": "keep-alive",
            "Host": target_url.host,
        }
        if request.content_type:
            headers["Content-Type"] = request.content_type
        cookie = request.headers.get("Cookie", "")
        initial_cookies = {"frc": self.state.frc, "map-md": self.state.map_md}
        cookie = _add_cookie_string(cookie, initial_cookies)
        self.state.proxy_cookie = _add_cookie_string(
            self.state.proxy_cookie, initial_cookies
        )
        if self.state.proxy_cookie:
            cookie = _add_cookie_string(cookie, _parse_cookie_string(self.state.proxy_cookie))
        headers["Cookie"] = cookie
        if referer := request.headers.get("Referer"):
            headers["Referer"] = self._rewrite_back(referer)
        if request.method == "POST":
            headers["Origin"] = f"https://www.{self.state.amazon_domain}"
        return headers

    def _capture_set_cookies(self, set_cookies: list[str]) -> None:
        """Capture upstream cookies into the proxy cookie string."""
        cookies = _parse_cookie_string(self.state.proxy_cookie)
        for cookie_header in set_cookies:
            name, value = _cookie_name_value(cookie_header)
            if name and not (name == "ap-fid" and value == '""'):
                cookies[name] = value
        self.state.proxy_cookie = _format_cookie_string(cookies)

    def _is_success_location(self, location: str) -> bool:
        """Return true if Amazon redirected to OAuth success."""
        return "/ap/maplanding?" in location or "/spa/index.html" in location

    def _capture_success(self, location: str) -> None:
        """Capture authorization data from a successful redirect."""
        query = parse_qs(URL(location).query_string)
        values = query.get("openid.oa2.authorization_code", [])
        if values:
            self.state.authorization_code = values[0]

    def _rewrite_to_proxy(self, location: str, request_host: str | None = None) -> str:
        """Rewrite Amazon redirects back through the HA proxy route."""
        if location.startswith("/"):
            host = request_host or f"www.{self.state.amazon_domain}"
            return f"{self.state.proxy_base_url}/{host}{location}"
        return (
            location.replace(
                f"https://www.{self.state.amazon_domain}/",
                f"{self.state.proxy_base_url}/www.{self.state.amazon_domain}/",
            )
            .replace(
                f"http://www.{self.state.amazon_domain}/",
                f"{self.state.proxy_base_url}/www.{self.state.amazon_domain}/",
            )
            .replace(
                f"https://alexa.{self.state.amazon_domain}/",
                f"{self.state.proxy_base_url}/alexa.{self.state.amazon_domain}/",
            )
        )

    def _rewrite_back(self, value: str) -> str:
        """Rewrite proxy URLs back to Amazon URLs for upstream headers/forms."""
        return (
            value.replace(
                f"{self.state.proxy_base_url}/www.{self.state.amazon_domain}/",
                f"https://www.{self.state.amazon_domain}/",
            )
            .replace(
                f"{self.state.proxy_base_url}/alexa.{self.state.amazon_domain}/",
                f"https://alexa.{self.state.amazon_domain}/",
            )
            .replace(f"{self.state.proxy_base_url}/", self.state.initial_url)
        )

    def _rewrite_body(self, body: bytes) -> bytes:
        """Rewrite Amazon links in response bodies to the proxy."""
        text = body.decode(errors="ignore").replace("&#x2F;", "/")
        text = text.replace(
            f"https://www.{self.state.amazon_domain}/",
            f"{self.state.proxy_base_url}/www.{self.state.amazon_domain}/",
        )
        text = text.replace(
            f"http://www.{self.state.amazon_domain}/",
            f"{self.state.proxy_base_url}/www.{self.state.amazon_domain}/",
        )
        text = text.replace(
            f"https://alexa.{self.state.amazon_domain}/",
            f"{self.state.proxy_base_url}/alexa.{self.state.amazon_domain}/",
        )
        return text.encode()

    def _response_headers(self, headers: Any) -> dict[str, str]:
        """Return safe response headers for Home Assistant."""
        skip = {"content-length", "content-encoding", "transfer-encoding", "connection"}
        result: dict[str, str] = {}
        for key, value in headers.items():
            lower = key.lower()
            if lower in skip:
                continue
            if lower == "set-cookie":
                value = value.replace("Secure", "")
            result[key] = value
        return result


async def complete_cookie2_login(
    session: ClientSession, state: Cookie2State
) -> dict[str, Any]:
    """Register the captured OAuth login and return alexa-cookie2-style data."""
    if not state.authorization_code:
        raise AmazonThermostatAuthError("Amazon login did not return an authorization code")

    login_data: dict[str, Any] = {
        "loginCookie": state.proxy_cookie,
        "authorization_code": state.authorization_code,
        "frc": state.frc,
        "map-md": state.map_md,
        "deviceId": state.device_id,
        "verifier": state.code_verifier,
        "deviceAppName": state.device_app_name,
        "deviceSerial": secrets.token_hex(16),
    }

    register_response = await _post_json(
        session,
        f"https://api.{state.amazon_domain}/auth/register",
        _register_payload(state, login_data),
        {
            "User-Agent": API_CALL_USER_AGENT,
            "Accept-Language": state.accept_language,
            "Accept-Charset": "utf-8",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Cookie": state.proxy_cookie,
            "Accept": "application/json",
            "x-amzn-identity-auth-domain": f"api.{state.amazon_domain}",
        },
    )
    try:
        tokens = register_response["response"]["success"]["tokens"]
        bearer = tokens["bearer"]
    except (KeyError, TypeError) as err:
        raise AmazonThermostatAuthError("Amazon device registration did not return tokens") from err

    login_data["refreshToken"] = bearer["refresh_token"]
    login_data["tokenDate"] = int(time.time() * 1000)
    login_data["macDms"] = tokens.get("mac_dms")
    local_cookie = await exchange_refresh_token_for_cookie(
        session,
        state.amazon_domain,
        login_data["refreshToken"],
        state.accept_language,
        state.device_app_name,
    )
    csrf_data = await get_csrf_from_cookie(
        session, state.amazon_domain, local_cookie, state.accept_language
    )
    login_data["amazonPage"] = state.amazon_domain
    login_data["localCookie"] = csrf_data["cookie"]
    login_data["csrf"] = csrf_data["csrf"]
    login_data["dataVersion"] = 2
    login_data.pop("authorization_code", None)
    login_data.pop("verifier", None)
    return login_data


async def refresh_cookie2_login(
    session: ClientSession,
    cookie_data: dict[str, Any],
    amazon_domain: str,
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE,
) -> dict[str, Any]:
    """Refresh stored cookie2 data."""
    refresh_token = cookie_data.get("refreshToken")
    if not refresh_token:
        raise AmazonThermostatAuthError("No refresh token available")
    local_cookie = await exchange_refresh_token_for_cookie(
        session,
        amazon_domain,
        refresh_token,
        accept_language,
        cookie_data.get("deviceAppName", DEFAULT_DEVICE_APP_NAME),
    )
    csrf_data = await get_csrf_from_cookie(session, amazon_domain, local_cookie, accept_language)
    updated = dict(cookie_data)
    updated["localCookie"] = csrf_data["cookie"]
    updated["csrf"] = csrf_data["csrf"]
    updated["tokenDate"] = int(time.time() * 1000)
    return updated


async def exchange_refresh_token_for_cookie(
    session: ClientSession,
    amazon_domain: str,
    refresh_token: str,
    accept_language: str,
    device_app_name: str,
) -> str:
    """Exchange refresh token for local Amazon website cookies."""
    data = {
        "di.os.name": "iOS",
        "app_version": API_CALL_VERSION,
        "domain": f".{amazon_domain}",
        "source_token": refresh_token,
        "requested_token_type": "auth_cookies",
        "source_token_type": "refresh_token",
        "di.hw.version": "iPhone",
        "di.sdk.version": "6.12.4",
        "app_name": device_app_name,
        "di.os.version": "16.6",
    }
    async with session.post(
        f"https://www.{amazon_domain}/ap/exchangetoken/cookies",
        data=urlencode(data),
        headers={
            "User-Agent": API_CALL_USER_AGENT,
            "Accept-Language": accept_language,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "x-amzn-identity-auth-domain": f"api.{amazon_domain}",
        },
    ) as response:
        body = await response.json(content_type=None)
    try:
        cookies = body["response"]["tokens"]["cookies"][f".{amazon_domain}"]
    except (KeyError, TypeError) as err:
        raise AmazonThermostatAuthError("Amazon token exchange did not return cookies") from err
    return _format_cookie_string({cookie["Name"]: cookie["Value"] for cookie in cookies})


async def get_csrf_from_cookie(
    session: ClientSession,
    amazon_domain: str,
    cookie: str,
    accept_language: str,
) -> dict[str, str | None]:
    """Fetch CSRF token from Alexa endpoints using a local cookie."""
    current_cookie = cookie
    for path in CSRF_PATHS:
        async with session.get(
            f"https://alexa.{amazon_domain}{path}",
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": accept_language,
                "Referer": f"https://alexa.{amazon_domain}/spa/index.html",
                "Origin": f"https://alexa.{amazon_domain}",
                "Cookie": current_cookie,
                "Accept": "*/*",
            },
        ) as response:
            set_cookies = response.headers.getall("Set-Cookie", [])
            current_cookie = _add_cookie_string(
                current_cookie,
                {
                    name: value
                    for header in set_cookies
                    for name, value in [_cookie_name_value(header)]
                    if name
                },
            )
        csrf = _parse_cookie_string(current_cookie).get("csrf")
        if csrf:
            return {"cookie": current_cookie, "csrf": csrf}
    return {"cookie": current_cookie, "csrf": None}


def _register_payload(state: Cookie2State, login_data: dict[str, Any]) -> dict[str, Any]:
    """Return the `/auth/register` payload."""
    cookies = _parse_cookie_string(login_data["loginCookie"])
    return {
        "requested_extensions": ["device_info", "customer_info"],
        "cookies": {
            "website_cookies": [
                {"Name": name, "Value": value} for name, value in cookies.items()
            ],
            "domain": f".{state.amazon_domain}",
        },
        "registration_data": {
            "domain": "Device",
            "app_version": API_CALL_VERSION,
            "device_type": DEVICE_TYPE,
            "device_name": "%FIRST_NAME%'s%DUPE_STRATEGY_1ST%" + state.device_app_name,
            "os_version": "16.6",
            "device_serial": login_data["deviceSerial"],
            "device_model": "iPhone",
            "app_name": state.device_app_name,
            "software_version": "1",
        },
        "auth_data": {
            "client_id": login_data["deviceId"],
            "authorization_code": login_data["authorization_code"],
            "code_verifier": login_data["verifier"],
            "code_algorithm": "SHA-256",
            "client_domain": "DeviceLegacy",
        },
        "user_context_map": {"frc": cookies.get("frc", state.frc)},
        "requested_token_type": ["bearer", "mac_dms", "website_cookies"],
    }


async def _post_json(
    session: ClientSession, url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """POST JSON and return response JSON."""
    async with session.post(url, json=payload, headers=headers) as response:
        if response.status >= 400:
            raise AmazonThermostatApiError(f"Amazon auth HTTP error: {response.status}")
        data = await response.json(content_type=None)
    if not isinstance(data, dict):
        raise AmazonThermostatAuthError("Amazon auth response was not JSON object")
    return data


def _base64url(value: bytes) -> str:
    """Return base64url without padding."""
    return urlsafe_b64encode(value).decode().rstrip("=")


def _cookie_name_value(cookie_header: str) -> tuple[str | None, str | None]:
    """Extract the first name/value from a Set-Cookie header."""
    first = cookie_header.split(";", 1)[0]
    if "=" not in first:
        return None, None
    name, value = first.split("=", 1)
    return name, value


def _parse_cookie_string(cookie: str) -> dict[str, str]:
    """Parse a semicolon-separated cookie string."""
    result: dict[str, str] = {}
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name and value:
            result[name] = value
    return result


def _format_cookie_string(cookies: dict[str, str]) -> str:
    """Format cookies for an HTTP Cookie header."""
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _add_cookie_string(cookie: str, updates: dict[str, str | None]) -> str:
    """Merge cookie updates into an existing cookie string."""
    cookies = _parse_cookie_string(cookie)
    for name, value in updates.items():
        if name and value is not None:
            cookies[name] = value
    return _format_cookie_string(cookies)

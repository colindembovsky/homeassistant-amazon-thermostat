"""Config flow for Amazon Thermostat."""

from __future__ import annotations

import datetime as dt
from hashlib import sha256
import html
import logging
from typing import Any

from aiohttp import web, web_response
from aiohttp.web_exceptions import HTTPBadRequest
import httpx
import voluptuous as vol
from yarl import URL

from homeassistant.components.http.view import HomeAssistantView
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import AlexaThermostatApi
from .auth import (
    AlexaPyAuthSessionProvider,
    ManualCookieAuthSessionProvider,
    create_alexapy_login,
)
from .const import (
    AUTH_CALLBACK_NAME,
    AUTH_CALLBACK_PATH,
    AUTH_METHOD_ALEXAPY,
    AUTH_METHOD_MANUAL,
    AUTH_PROXY_NAME,
    AUTH_PROXY_PATH,
    CONF_AMAZON_DOMAIN,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_CSRF,
    CONF_EMAIL,
    CONF_HASS_URL,
    CONF_OAUTH,
    CONF_OTPSECRET,
    CONF_POLL_INTERVAL,
    CONF_PUBLIC_URL,
    DEFAULT_AMAZON_DOMAIN,
    DEFAULT_HASS_URL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    SUPPORTED_AMAZON_DOMAINS,
)
from .models import AmazonThermostatAuthError, AmazonThermostatError

_LOGGER = logging.getLogger(__name__)


class AmazonThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an Amazon Thermostat config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._flow_data: dict[str, Any] = {}
        self._login: Any | None = None
        self._proxy: Any | None = None
        self._proxy_view: AmazonThermostatAuthorizationProxyView | None = None
        self._reauth_entry: Any | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose region and auth method."""
        if user_input is not None:
            self._flow_data.update(user_input)
            if user_input[CONF_AUTH_METHOD] == AUTH_METHOD_MANUAL:
                return await self.async_step_manual()
            return await self.async_step_alexapy_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AMAZON_DOMAIN, default=DEFAULT_AMAZON_DOMAIN
                    ): vol.In(SUPPORTED_AMAZON_DOMAINS),
                    vol.Required(
                        CONF_AUTH_METHOD, default=AUTH_METHOD_ALEXAPY
                    ): vol.In(
                        {
                            AUTH_METHOD_ALEXAPY: "Guided Amazon login",
                            AUTH_METHOD_MANUAL: "Advanced: manual cookie and CSRF",
                        }
                    ),
                    vol.Required(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                    ),
                }
            ),
        )

    async def async_step_alexapy_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and start alexapy proxy login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_data.update(user_input)
            try:
                self._login = create_alexapy_login(
                    self._flow_data[CONF_AMAZON_DOMAIN],
                    self._flow_data,
                    self.hass.config.path,
                )
                return await self._async_start_proxy()
            except AmazonThermostatAuthError:
                errors["base"] = "alexapy_not_installed"
            except ValueError:
                errors["base"] = "invalid_url"

        return self.async_show_form(
            step_id="alexapy_credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._flow_data.get(CONF_EMAIL, "")): str,
                    vol.Required(CONF_PASSWORD, default=self._flow_data.get(CONF_PASSWORD, "")): str,
                    vol.Optional(
                        CONF_OTPSECRET, default=self._flow_data.get(CONF_OTPSECRET, "")
                    ): str,
                    vol.Optional(CONF_HASS_URL, default=self._default_hass_url()): str,
                    vol.Optional(CONF_PUBLIC_URL, default=self._default_public_url()): str,
                }
            ),
            errors=errors,
        )

    async def async_step_check_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete the external login step."""
        if self._proxy_view:
            self._proxy_view.reset()
        return self.async_external_step_done(next_step_id="finish_proxy")

    async def async_step_finish_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish alexapy proxy auth."""
        if self._login is None:
            return self.async_abort(reason="login_failed")

        if not await self._login.test_loggedin():
            return self.async_abort(reason="login_failed")

        await self._login.finalize_login()
        provider = AlexaPyAuthSessionProvider(self._login)
        try:
            await AlexaThermostatApi(provider).async_validate_auth()
        except AmazonThermostatAuthError:
            return self.async_abort(reason="login_failed")
        except AmazonThermostatError:
            return self.async_abort(reason="cannot_connect")

        unique_id = _account_unique_id(
            self._login.customer_id or self._login.email,
            self._flow_data[CONF_AMAZON_DOMAIN],
        )
        await self.async_set_unique_id(unique_id)
        entry_data = self._entry_data_from_alexapy_login()
        if self._reauth_entry:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates=entry_data,
            )
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{self._login.email} ({self._flow_data[CONF_AMAZON_DOMAIN]})",
            data=entry_data,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle advanced manual cookie setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_data.update(user_input)
            provider = ManualCookieAuthSessionProvider(
                async_get_clientsession(self.hass),
                self._flow_data[CONF_AMAZON_DOMAIN],
                self._flow_data[CONF_COOKIE],
                self._flow_data[CONF_CSRF],
            )
            try:
                await AlexaThermostatApi(provider).async_validate_auth()
            except AmazonThermostatAuthError:
                errors["base"] = "invalid_auth"
            except AmazonThermostatError:
                errors["base"] = "cannot_connect"
            else:
                unique_id = _manual_unique_id(
                    self._flow_data[CONF_COOKIE], self._flow_data[CONF_AMAZON_DOMAIN]
                )
                await self.async_set_unique_id(unique_id)
                entry_data = {
                    CONF_AUTH_METHOD: AUTH_METHOD_MANUAL,
                    CONF_AMAZON_DOMAIN: self._flow_data[CONF_AMAZON_DOMAIN],
                    CONF_COOKIE: self._flow_data[CONF_COOKIE],
                    CONF_CSRF: self._flow_data[CONF_CSRF],
                    CONF_POLL_INTERVAL: self._flow_data[CONF_POLL_INTERVAL],
                }
                if self._reauth_entry:
                    return self.async_update_reload_and_abort(
                        self._reauth_entry,
                        data_updates=entry_data,
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Amazon Thermostat ({self._flow_data[CONF_AMAZON_DOMAIN]})",
                    data=entry_data,
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COOKIE): str,
                    vol.Required(CONF_CSRF): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._flow_data.update(entry_data)
        self._reauth_entry = self._get_reauth_entry()
        self.context["title_placeholders"] = {
            "amazon_domain": entry_data.get(CONF_AMAZON_DOMAIN, DEFAULT_AMAZON_DOMAIN)
        }
        if entry_data.get(CONF_AUTH_METHOD) == AUTH_METHOD_ALEXAPY:
            return await self.async_step_alexapy_credentials()
        return await self.async_step_manual()

    async def _async_start_proxy(self) -> ConfigFlowResult:
        """Start alexapy's browser-based proxy login."""
        try:
            from alexapy import AlexaProxy
        except ImportError as err:
            raise AmazonThermostatAuthError("alexapy is not installed") from err

        hass_url = self._flow_data.get(CONF_HASS_URL) or self._default_hass_url()
        self._proxy = AlexaProxy(
            self._login,
            str(URL(hass_url).with_path(AUTH_PROXY_PATH)),
        )
        self._proxy.session_factory = lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
        )
        self._proxy.change_login(self._login)
        if not self._proxy_view:
            self._proxy_view = AmazonThermostatAuthorizationProxyView(
                self._proxy.all_handler
            )
        else:
            self._proxy_view.handler = self._proxy.all_handler

        self.hass.http.register_view(AmazonThermostatAuthorizationCallbackView())
        self.hass.http.register_view(self._proxy_view)

        callback_url = (
            URL(hass_url)
            .with_path(AUTH_CALLBACK_PATH)
            .with_query({"flow_id": self.flow_id})
        )
        proxy_url = self._proxy.access_url().with_query(
            {"config_flow_id": self.flow_id, "callback_url": str(callback_url)}
        )
        self._login._session.cookie_jar.clear()
        self._login.proxy_url = proxy_url
        return self.async_external_step(step_id="check_proxy", url=str(proxy_url))

    def _entry_data_from_alexapy_login(self) -> dict[str, Any]:
        """Build config-entry data from a successful alexapy login."""
        return {
            CONF_AUTH_METHOD: AUTH_METHOD_ALEXAPY,
            CONF_AMAZON_DOMAIN: self._flow_data[CONF_AMAZON_DOMAIN],
            CONF_EMAIL: self._login.email,
            CONF_PASSWORD: self._login.password,
            CONF_OTPSECRET: self._flow_data.get(CONF_OTPSECRET, ""),
            CONF_POLL_INTERVAL: self._flow_data[CONF_POLL_INTERVAL],
            CONF_HASS_URL: self._flow_data.get(CONF_HASS_URL),
            CONF_PUBLIC_URL: self._flow_data.get(CONF_PUBLIC_URL),
            CONF_OAUTH: {
                "access_token": self._login.access_token,
                "refresh_token": self._login.refresh_token,
                "expires_in": self._login.expires_in,
                "mac_dms": self._login.mac_dms,
                "code_verifier": self._login.code_verifier,
                "authorization_code": self._login.authorization_code,
            },
        }

    def _default_hass_url(self) -> str:
        """Return a Home Assistant URL suitable for proxy callbacks."""
        try:
            return get_url(self.hass, allow_external=False)
        except NoURLAvailableError:
            return self._flow_data.get(CONF_HASS_URL, DEFAULT_HASS_URL)

    def _default_public_url(self) -> str:
        """Return the external Home Assistant URL when available."""
        try:
            url = get_url(self.hass, allow_internal=False)
        except NoURLAvailableError:
            return self._flow_data.get(CONF_PUBLIC_URL, "")
        return url if url.endswith("/") else f"{url}/"


class AmazonThermostatAuthorizationCallbackView(HomeAssistantView):
    """Handle callback from alexapy external auth."""

    url = AUTH_CALLBACK_PATH
    name = AUTH_CALLBACK_NAME
    requires_auth = False

    async def get(self, request: web.Request) -> web_response.Response:
        """Receive authorization confirmation."""
        hass = request.app["hass"]
        try:
            await hass.config_entries.flow.async_configure(
                flow_id=request.query["flow_id"], user_input=None
            )
        except (KeyError, UnknownFlow) as ex:
            raise HTTPBadRequest() from ex
        return web_response.Response(
            headers={"content-type": "text/html"},
            text="<script>window.close()</script>Success! This window can be closed.",
        )


class AmazonThermostatAuthorizationProxyView(HomeAssistantView):
    """Handle proxied Amazon login connections."""

    url = AUTH_PROXY_PATH
    extra_urls = [f"{AUTH_PROXY_PATH}/{{tail:.*}}"]
    name = AUTH_PROXY_NAME
    requires_auth = False
    handler: web.RequestHandler | None = None
    known_ips: dict[str, dt.datetime] = {}
    auth_seconds = 300

    def __init__(self, handler: web.RequestHandler) -> None:
        """Initialize proxy routes."""
        AmazonThermostatAuthorizationProxyView.handler = handler
        for method in ("get", "post", "delete", "put", "patch", "head", "options"):
            setattr(self, method, self.check_auth())

    @classmethod
    def check_auth(cls):
        """Wrap proxy access so only the active config flow can use it."""

        async def wrapped(request: web.Request, **kwargs: Any) -> web.StreamResponse:
            hass = request.app["hass"]
            if not cls._request_is_authorized(hass, request):
                raise Unauthorized()
            try:
                assert cls.handler is not None
                return await cls.handler(request, **kwargs)
            except web.HTTPException:
                raise
            except httpx.ConnectError as ex:
                _LOGGER.warning("Amazon auth proxy connection error: %s", ex)
                return web_response.Response(
                    headers={"content-type": "text/html"},
                    text="Connection error during Amazon login. Please refresh and try again.",
                )
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning("Amazon auth proxy error: %s", ex, exc_info=True)
                return web_response.Response(
                    headers={"content-type": "text/html"},
                    text=(
                        "Unexpected error during Amazon login. Please try again."
                        f"<br /><pre>{html.escape(type(ex).__name__)}</pre>"
                    ),
                )

        return wrapped

    @classmethod
    def _request_is_authorized(cls, hass: Any, request: web.Request) -> bool:
        """Return whether the proxy request belongs to an active flow."""
        if (
            request.remote in cls.known_ips
            and (dt.datetime.now() - cls.known_ips[request.remote]).seconds
            <= cls.auth_seconds
        ):
            return True
        flow_id = request.url.query.get("config_flow_id")
        if not flow_id:
            return False
        for flow in hass.config_entries.flow.async_progress():
            if flow["flow_id"] == flow_id and flow["handler"] == DOMAIN:
                cls.known_ips[request.remote] = dt.datetime.now()
                return True
        return False

    @classmethod
    def reset(cls) -> None:
        """Reset authorized proxy clients."""
        cls.known_ips = {}


def _account_unique_id(account_id: str, amazon_domain: str) -> str:
    """Build a stable unique ID without exposing account details."""
    return f"{amazon_domain}:{sha256(account_id.encode()).hexdigest()}"


def _manual_unique_id(cookie: str, amazon_domain: str) -> str:
    """Build a stable-ish manual-auth unique ID without storing raw cookie in it."""
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name in {"ubid-main", "ubid-acbde", "ubid-acbuk"} and value:
            return _account_unique_id(f"{name}:{value}", amazon_domain)
    return _account_unique_id(cookie, amazon_domain)

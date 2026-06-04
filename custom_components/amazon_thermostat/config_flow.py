"""Config flow for Amazon Thermostat."""

from __future__ import annotations

from hashlib import sha256
import logging
from typing import Any

from aiohttp import web, web_response
from aiohttp.web_exceptions import HTTPBadRequest
import voluptuous as vol
from yarl import URL

from homeassistant.components.http.view import HomeAssistantView
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlexaThermostatApi
from .auth import Cookie2AuthSessionProvider
from .const import (
    AUTH_CALLBACK_NAME,
    AUTH_CALLBACK_PATH,
    AUTH_METHOD_COOKIE2,
    CONF_AMAZON_DOMAIN,
    CONF_AUTH_METHOD,
    CONF_COOKIE_DATA,
    CONF_HASS_URL,
    CONF_POLL_INTERVAL,
    CONF_PROXY_PORT,
    CONF_PUBLIC_URL,
    DEFAULT_AMAZON_DOMAIN,
    DEFAULT_HASS_URL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROXY_PORT,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    SUPPORTED_AMAZON_DOMAINS,
)
from .cookie2 import Cookie2ProxyServer, Cookie2State, complete_cookie2_login
from .models import AmazonThermostatAuthError, AmazonThermostatError

_LOGGER = logging.getLogger(__name__)


class AmazonThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an Amazon Thermostat config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._flow_data: dict[str, Any] = {}
        self._cookie2_state: Cookie2State | None = None
        self._cookie2_server: Cookie2ProxyServer | None = None
        self._reauth_entry: Any | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose region and polling interval, then start guided login."""
        if user_input is not None:
            self._flow_data.update(user_input)
            self._flow_data[CONF_AUTH_METHOD] = AUTH_METHOD_COOKIE2
            return await self.async_step_cookie2_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AMAZON_DOMAIN, default=DEFAULT_AMAZON_DOMAIN
                    ): vol.In(SUPPORTED_AMAZON_DOMAINS),
                    vol.Required(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                    ),
                }
            ),
        )

    async def async_step_cookie2_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect proxy settings and start the cookie2-compatible login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_data.update(user_input)
            try:
                return await self._async_start_cookie2_proxy()
            except OSError:
                errors["base"] = "proxy_port_in_use"
            except ValueError:
                errors["base"] = "invalid_url"

        return self.async_show_form(
            step_id="cookie2_credentials",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_HASS_URL, default=self._default_hass_url()): str,
                    vol.Optional(
                        CONF_PUBLIC_URL, default=self._default_public_url()
                    ): str,
                    vol.Required(
                        CONF_PROXY_PORT, default=self._default_proxy_port()
                    ): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
                }
            ),
            errors=errors,
        )

    async def async_step_check_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete the external login step."""
        return self.async_external_step_done(next_step_id="finish_proxy")

    async def async_step_finish_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish browser proxy auth."""
        return await self._async_finish_cookie2_proxy()

    async def _async_finish_cookie2_proxy(self) -> ConfigFlowResult:
        """Finish the cookie2-compatible proxy auth."""
        if self._cookie2_state is None or self._cookie2_server is None:
            return self.async_abort(reason="login_failed")

        proxy_session = self._cookie2_server.session
        runtime_session = async_get_clientsession(self.hass)
        try:
            if proxy_session is None:
                return self.async_abort(reason="login_failed")
            cookie_data = await complete_cookie2_login(
                proxy_session, self._cookie2_state
            )
            provider = Cookie2AuthSessionProvider(
                runtime_session,
                self._flow_data[CONF_AMAZON_DOMAIN],
                cookie_data,
            )
            await AlexaThermostatApi(provider).async_validate_auth()
        except AmazonThermostatAuthError:
            return self.async_abort(reason="login_failed")
        except AmazonThermostatError:
            return self.async_abort(reason="cannot_connect")
        finally:
            await self._cookie2_server.stop()
            self._cookie2_server = None

        unique_id = _account_unique_id(
            cookie_data.get("refreshToken") or cookie_data.get("deviceId", ""),
            self._flow_data[CONF_AMAZON_DOMAIN],
        )
        await self.async_set_unique_id(unique_id)
        entry_data = {
            CONF_AUTH_METHOD: AUTH_METHOD_COOKIE2,
            CONF_AMAZON_DOMAIN: self._flow_data[CONF_AMAZON_DOMAIN],
            CONF_COOKIE_DATA: cookie_data,
            CONF_POLL_INTERVAL: self._flow_data[CONF_POLL_INTERVAL],
            CONF_HASS_URL: self._flow_data.get(CONF_HASS_URL),
            CONF_PUBLIC_URL: self._flow_data.get(CONF_PUBLIC_URL),
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

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._flow_data.update(entry_data)
        self._flow_data[CONF_AUTH_METHOD] = AUTH_METHOD_COOKIE2
        self._flow_data.setdefault(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._reauth_entry = self._get_reauth_entry()
        self.context["title_placeholders"] = {
            "amazon_domain": entry_data.get(CONF_AMAZON_DOMAIN, DEFAULT_AMAZON_DOMAIN)
        }
        return await self.async_step_cookie2_credentials()

    async def _async_start_cookie2_proxy(self) -> ConfigFlowResult:
        """Start the standalone root-mounted cookie2 browser proxy.

        The proxy is served at the root of its own host/port (like the
        Homebridge alexa-cookie2 proxy) so Amazon's root-relative CVF requests
        are intercepted. A sub-path proxy cannot capture those.
        """
        public_url = self._flow_data.get(CONF_PUBLIC_URL) or self._flow_data.get(
            CONF_HASS_URL
        )
        hass_url = public_url or self._default_hass_url()
        proxy_host = URL(hass_url).host
        if not proxy_host:
            raise ValueError("Could not determine proxy host from Home Assistant URL")
        proxy_port = int(self._flow_data.get(CONF_PROXY_PORT, DEFAULT_PROXY_PORT))
        proxy_base_url = f"http://{proxy_host}:{proxy_port}"
        callback_url = str(
            URL(self._flow_data.get(CONF_HASS_URL) or hass_url)
            .with_path(AUTH_CALLBACK_PATH)
            .with_query({"flow_id": self.flow_id})
        )
        self._cookie2_state = Cookie2State(
            amazon_domain=self._flow_data[CONF_AMAZON_DOMAIN],
            proxy_base_url=proxy_base_url,
            callback_url=callback_url,
            flow_id=self.flow_id,
        )

        if self._cookie2_server is not None:
            await self._cookie2_server.stop()
        self._cookie2_server = Cookie2ProxyServer(self._cookie2_state)
        # Raises OSError if the port is already in use; handled by the caller.
        await self._cookie2_server.start("0.0.0.0", proxy_port)

        # The proxy redirects the browser to this Home Assistant callback to
        # resume the config flow once the OAuth code is captured.
        self.hass.http.register_view(AmazonThermostatAuthorizationCallbackView())

        return self.async_external_step(
            step_id="check_proxy", url=self._cookie2_state.entry_url
        )

    def _default_hass_url(self) -> str:
        """Return a Home Assistant URL suitable for proxy callbacks."""
        return self._flow_data.get(CONF_HASS_URL, DEFAULT_HASS_URL)

    def _default_public_url(self) -> str:
        """Return the external Home Assistant URL when available."""
        return self._flow_data.get(CONF_PUBLIC_URL, "")

    def _default_proxy_port(self) -> int:
        """Return the proxy port for the standalone login server."""
        return int(self._flow_data.get(CONF_PROXY_PORT, DEFAULT_PROXY_PORT))


class AmazonThermostatAuthorizationCallbackView(HomeAssistantView):
    """Handle the browser callback that resumes the guided login flow."""

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


def _account_unique_id(account_id: str, amazon_domain: str) -> str:
    """Build a stable unique ID without exposing account details."""
    return f"{amazon_domain}:{sha256(account_id.encode()).hexdigest()}"

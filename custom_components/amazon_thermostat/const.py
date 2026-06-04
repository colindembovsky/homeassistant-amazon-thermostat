"""Constants for the Amazon Thermostat integration."""

from __future__ import annotations

try:
    from homeassistant.const import Platform
except ModuleNotFoundError:
    class Platform:  # type: ignore[no-redef]
        """Fallback used only for pure unit tests without Home Assistant installed."""

        CLIMATE = "climate"
        SENSOR = "sensor"

DOMAIN = "amazon_thermostat"

CONF_AMAZON_DOMAIN = "amazon_domain"
CONF_AUTH_METHOD = "auth_method"
CONF_HASS_URL = "hass_url"
CONF_POLL_INTERVAL = "poll_interval"
CONF_PROXY_PORT = "proxy_port"
CONF_PUBLIC_URL = "public_url"

AUTH_METHOD_COOKIE2 = "cookie2"
CONF_COOKIE_DATA = "cookie_data"

AUTH_CALLBACK_NAME = f"{DOMAIN}_auth_callback"
AUTH_CALLBACK_PATH = f"/auth/{DOMAIN}/callback"

DEFAULT_AMAZON_DOMAIN = "amazon.com"
DEFAULT_HASS_URL = "http://homeassistant.local:8123/"
DEFAULT_PROXY_PORT = 8124
DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 60
MAX_POLL_INTERVAL = 3600

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]

SUPPORTED_AMAZON_DOMAINS = [
    "amazon.com",
    "amazon.ca",
    "amazon.de",
    "amazon.es",
    "amazon.fr",
    "amazon.it",
    "amazon.in",
    "amazon.nl",
    "amazon.co.jp",
    "amazon.co.uk",
    "amazon.com.au",
    "amazon.com.br",
    "amazon.com.mx",
]

USER_AGENT = (
    "AppleWebKit PitanguiBridge/2.2.595606.0-"
    "[HARDWARE=iPhone14_7][SOFTWARE=17.4.1][DEVICE=iPhone] AlexaRemote/8.x.x"
)

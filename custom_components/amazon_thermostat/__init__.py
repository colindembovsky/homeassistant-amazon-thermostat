"""Amazon Thermostat integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AlexaThermostatApi
from .auth import build_auth_provider_from_entry
from .const import AUTH_METHOD_COOKIE2, CONF_AUTH_METHOD, CONF_COOKIE_DATA, PLATFORMS
from .coordinator import AmazonThermostatConfigEntry, AmazonThermostatCoordinator
from .models import AmazonThermostatApiError


async def async_setup_entry(
    hass: HomeAssistant, entry: AmazonThermostatConfigEntry
) -> bool:
    """Set up Amazon Thermostat from a config entry."""
    if (
        entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_COOKIE2) != AUTH_METHOD_COOKIE2
        or CONF_COOKIE_DATA not in entry.data
    ):
        raise ConfigEntryAuthFailed(
            "This authentication method is no longer supported. Please sign in again."
        )

    api = AlexaThermostatApi(
        build_auth_provider_from_entry(
            async_get_clientsession(hass),
            entry.data,
        )
    )
    coordinator = AmazonThermostatCoordinator(hass, entry, api)

    try:
        await coordinator.async_config_entry_first_refresh()
    except AmazonThermostatApiError as err:
        raise ConfigEntryNotReady("Could not initialize Amazon Thermostat") from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AmazonThermostatConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

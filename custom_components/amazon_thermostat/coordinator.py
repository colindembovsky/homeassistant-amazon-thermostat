"""Data coordinator for Amazon thermostats."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AlexaThermostatApi
from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN
from .models import (
    AlexaThermostatData,
    AlexaThermostatDevice,
    AmazonThermostatApiError,
    AmazonThermostatAuthError,
    AmazonThermostatError,
    AmazonThermostatThrottleError,
)

_LOGGER = logging.getLogger(__name__)

AmazonThermostatConfigEntry: TypeAlias = ConfigEntry["AmazonThermostatCoordinator"]


class AmazonThermostatCoordinator(
    DataUpdateCoordinator[dict[str, AlexaThermostatData]]
):
    """Coordinator for all thermostats in one Amazon account."""

    config_entry: AmazonThermostatConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: AmazonThermostatConfigEntry,
        api: AlexaThermostatApi,
    ) -> None:
        """Initialize the coordinator."""
        interval = int(
            config_entry.options.get(
                CONF_POLL_INTERVAL,
                config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.devices: dict[str, AlexaThermostatDevice] = {}

    async def _async_update_data(self) -> dict[str, AlexaThermostatData]:
        """Fetch thermostat data from Alexa."""
        try:
            if not self.devices:
                discovered = await self.api.async_discover_thermostats()
                self.devices = {device.endpoint_id: device for device in discovered}

            states: dict[str, AlexaThermostatData] = {}
            for endpoint_id, device in self.devices.items():
                states[endpoint_id] = await self.api.async_get_thermostat_state(device)
            return states
        except AmazonThermostatAuthError as err:
            raise ConfigEntryAuthFailed("Amazon credentials expired or invalid") from err
        except AmazonThermostatThrottleError as err:
            raise UpdateFailed("Amazon is rate limiting thermostat requests") from err
        except (AmazonThermostatApiError, AmazonThermostatError) as err:
            raise UpdateFailed(f"Error communicating with Amazon: {err}") from err

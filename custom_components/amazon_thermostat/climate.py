"""Climate platform for Amazon thermostats."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmazonThermostatConfigEntry, AmazonThermostatCoordinator
from .models import AlexaThermostatData, AmazonThermostatError

ALEXA_TO_HA_MODE = {
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "AUTO": HVACMode.HEAT_COOL,
    "ECO": HVACMode.HEAT_COOL,
    "OFF": HVACMode.OFF,
}

HA_TO_ALEXA_MODE = {
    HVACMode.HEAT: "HEAT",
    HVACMode.COOL: "COOL",
    HVACMode.HEAT_COOL: "AUTO",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonThermostatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Amazon thermostat climate entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        AmazonThermostatClimate(coordinator, endpoint_id)
        for endpoint_id in coordinator.data
    )


class AmazonThermostatClimate(
    CoordinatorEntity[AmazonThermostatCoordinator], ClimateEntity
):
    """Amazon thermostat climate entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_ECO]
    _attr_target_temperature_step = 1

    def __init__(
        self, coordinator: AmazonThermostatCoordinator, endpoint_id: str
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._endpoint_id = endpoint_id
        data = self._data
        self._attr_unique_id = endpoint_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, endpoint_id)},
            name=data.device.name,
            manufacturer=data.device.manufacturer or "Amazon",
            model=data.device.model,
            serial_number=data.device.serial_number,
        )

    @property
    def _data(self) -> AlexaThermostatData:
        """Return the latest coordinator data for this thermostat."""
        return self.coordinator.data[self._endpoint_id]

    @property
    def _has_range(self) -> bool:
        """Return whether the thermostat exposes a heat/cool setpoint range."""
        return (
            self.hvac_mode == HVACMode.HEAT_COOL
            and self._data.lower_setpoint is not None
            and self._data.upper_setpoint is not None
        )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported climate features for this thermostat.

        Heat/cool mode advertises a target temperature *range* (dual setpoint)
        while single modes advertise a single target temperature. Exposing both
        at once makes the frontend render a single setpoint slider instead of
        the dual heat/cool handles.
        """
        features = ClimateEntityFeature.PRESET_MODE
        if self._has_range:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def temperature_unit(self) -> str:
        """Return native temperature unit."""
        if self._data.native_scale == "CELSIUS":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return self._data.current_temperature.value if self._data.current_temperature else None

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity."""
        return self._data.current_humidity

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature.

        Returns ``None`` in heat/cool mode so the frontend renders the dual
        low/high handles instead of a single setpoint slider.
        """
        if self._has_range:
            return None
        return self._data.target_setpoint.value if self._data.target_setpoint else None

    @property
    def target_temperature_low(self) -> float | None:
        """Return low target temperature."""
        if not self._has_range:
            return None
        return self._data.lower_setpoint.value if self._data.lower_setpoint else None

    @property
    def target_temperature_high(self) -> float | None:
        """Return high target temperature."""
        if not self._has_range:
            return None
        return self._data.upper_setpoint.value if self._data.upper_setpoint else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode."""
        return ALEXA_TO_HA_MODE.get(self._data.thermostat_mode)

    @property
    def preset_mode(self) -> str:
        """Return current preset mode."""
        if self._data.thermostat_mode == "ECO":
            return PRESET_ECO
        return PRESET_NONE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode.

        These Alexa thermostats do not implement the PowerController, so a
        dedicated ``turnOff``/``turnOn`` is rejected with ``BAD_REQUEST``.
        Setting ``thermostatMode`` (including ``OFF``) is the supported path,
        matching the Homebridge plugin's behavior for non-power devices.
        """
        alexa_mode = "OFF" if hvac_mode == HVACMode.OFF else HA_TO_ALEXA_MODE.get(hvac_mode)
        if alexa_mode is None:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")
        try:
            await self.coordinator.api.async_set_thermostat_mode(
                self._endpoint_id, alexa_mode
            )
        except AmazonThermostatError as err:
            raise HomeAssistantError(f"Failed to set HVAC mode: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        if preset_mode == PRESET_NONE:
            return
        if preset_mode != PRESET_ECO:
            raise HomeAssistantError(f"Unsupported preset mode: {preset_mode}")
        try:
            await self.coordinator.api.async_set_thermostat_mode(self._endpoint_id, "ECO")
        except AmazonThermostatError as err:
            raise HomeAssistantError(f"Failed to set preset mode: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        scale = self._data.native_scale
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)

        try:
            if low is not None or high is not None or self.hvac_mode == HVACMode.HEAT_COOL:
                await self._async_set_temperature_range(low, high, scale)
            elif (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
                await self.coordinator.api.async_set_target_temperature(
                    self._endpoint_id, float(temperature), scale
                )
        except AmazonThermostatError as err:
            raise HomeAssistantError(f"Failed to set temperature: {err}") from err
        await self.coordinator.async_request_refresh()

    async def _async_set_temperature_range(
        self,
        low: float | None,
        high: float | None,
        scale: str,
    ) -> None:
        """Set a dual heat/cool target range."""
        current_low = self.target_temperature_low
        current_high = self.target_temperature_high
        target_low = low if low is not None else current_low
        target_high = high if high is not None else current_high
        if target_low is None or target_high is None:
            raise HomeAssistantError("Both low and high target temperatures are required")
        await self.coordinator.api.async_set_temperature_range(
            self._endpoint_id, float(target_low), float(target_high), scale
        )

    async def async_turn_on(self) -> None:
        """Turn on the thermostat (heat/cool) via thermostat mode."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        """Turn off the thermostat via thermostat mode."""
        await self.async_set_hvac_mode(HVACMode.OFF)

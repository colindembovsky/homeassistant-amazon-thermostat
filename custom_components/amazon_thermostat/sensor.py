"""Sensor platform for Amazon thermostats."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmazonThermostatConfigEntry, AmazonThermostatCoordinator
from .models import AlexaThermostatData


@dataclass(frozen=True, kw_only=True)
class AmazonThermostatSensorDescription(SensorEntityDescription):
    """Describes an Amazon thermostat sensor."""

    value_fn: Callable[[AlexaThermostatData], float | None]


SENSORS: tuple[AmazonThermostatSensorDescription, ...] = (
    AmazonThermostatSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.current_temperature.value
        if data.current_temperature is not None
        else None,
    ),
    AmazonThermostatSensorDescription(
        key="indoor_humidity",
        translation_key="indoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.current_humidity,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonThermostatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Amazon thermostat sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        AmazonThermostatSensor(coordinator, endpoint_id, description)
        for endpoint_id, data in coordinator.data.items()
        for description in SENSORS
        if description.value_fn(data) is not None
    )


class AmazonThermostatSensor(
    CoordinatorEntity[AmazonThermostatCoordinator], SensorEntity
):
    """Amazon thermostat sensor entity."""

    _attr_has_entity_name = True
    entity_description: AmazonThermostatSensorDescription

    def __init__(
        self,
        coordinator: AmazonThermostatCoordinator,
        endpoint_id: str,
        description: AmazonThermostatSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._endpoint_id = endpoint_id
        self.entity_description = description
        data = self._data
        self._attr_unique_id = f"{endpoint_id}_{description.key}"
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
    def native_unit_of_measurement(self) -> str | None:
        """Return native unit for dynamic temperature sensors."""
        if self.entity_description.key == "current_temperature":
            return (
                UnitOfTemperature.CELSIUS
                if self._data.native_scale == "CELSIUS"
                else UnitOfTemperature.FAHRENHEIT
            )
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> float | None:
        """Return sensor value."""
        return self.entity_description.value_fn(self._data)


"""Models and parsers for Alexa thermostat data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

TemperatureScale = Literal["FAHRENHEIT", "CELSIUS", "fahrenheit", "celsius"]
ThermostatMode = Literal["HEAT", "COOL", "AUTO", "ECO", "OFF"]

ENDPOINT_PREFIX = "amzn1.alexa.endpoint."
HUMIDITY_RANGE_NAME = "Indoor humidity"


class AmazonThermostatError(Exception):
    """Base exception for the integration."""


class AmazonThermostatAuthError(AmazonThermostatError):
    """Raised when Amazon authentication fails."""


class AmazonThermostatThrottleError(AmazonThermostatError):
    """Raised when Amazon rate limits requests."""


class AmazonThermostatApiError(AmazonThermostatError):
    """Raised when the Amazon API returns an error."""


class AmazonThermostatInvalidResponse(AmazonThermostatError):
    """Raised when Amazon returns an unexpected response shape."""


@dataclass(frozen=True)
class AlexaTemperature:
    """Alexa temperature value."""

    value: float
    scale: TemperatureScale

    @property
    def normalized_scale(self) -> Literal["FAHRENHEIT", "CELSIUS"]:
        """Return the scale in Alexa's uppercase write format."""
        scale = self.scale.upper()
        if scale not in {"FAHRENHEIT", "CELSIUS"}:
            raise AmazonThermostatInvalidResponse(f"Unsupported temperature scale: {self.scale}")
        return scale  # type: ignore[return-value]


@dataclass(frozen=True)
class AlexaThermostatDevice:
    """Discovered Alexa thermostat endpoint."""

    endpoint_id: str
    device_id: str
    name: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    supported_operations: frozenset[str]


@dataclass(frozen=True)
class AlexaThermostatData:
    """Parsed Alexa thermostat state."""

    device: AlexaThermostatDevice
    current_temperature: AlexaTemperature | None
    target_setpoint: AlexaTemperature | None
    lower_setpoint: AlexaTemperature | None
    upper_setpoint: AlexaTemperature | None
    thermostat_mode: ThermostatMode | None
    current_humidity: float | None
    online: bool = True

    @property
    def native_scale(self) -> Literal["FAHRENHEIT", "CELSIUS"]:
        """Return the best available native scale for writes."""
        for value in (
            self.current_temperature,
            self.target_setpoint,
            self.lower_setpoint,
            self.upper_setpoint,
        ):
            if value is not None:
                return value.normalized_scale
        return "FAHRENHEIT"


def _text_value(value: Any) -> str | None:
    """Extract Amazon nested text value."""
    if not isinstance(value, dict):
        return None
    text = value.get("value", {}).get("text")
    return text if isinstance(text, str) else None


def _parse_temperature(value: Any) -> AlexaTemperature | None:
    """Parse an Alexa temperature object."""
    if not isinstance(value, dict):
        return None
    raw_value = value.get("value")
    raw_scale = value.get("scale")
    if not isinstance(raw_value, (int, float, str)) or not isinstance(raw_scale, str):
        return None
    try:
        temp_value = float(raw_value)
    except ValueError:
        return None
    if raw_scale.upper() not in {"FAHRENHEIT", "CELSIUS"}:
        return None
    return AlexaTemperature(temp_value, raw_scale)  # type: ignore[arg-type]


def _endpoint_device_id(endpoint_id: str) -> str:
    """Return a stable compact device ID for an endpoint."""
    if endpoint_id.startswith(ENDPOINT_PREFIX):
        return endpoint_id.removeprefix(ENDPOINT_PREFIX)
    return endpoint_id


def parse_discovery_response(response: dict[str, Any]) -> list[AlexaThermostatDevice]:
    """Parse Alexa endpoint discovery into thermostat devices."""
    try:
        endpoints = response["data"]["endpoints"]["items"]
    except (KeyError, TypeError) as err:
        raise AmazonThermostatInvalidResponse("Missing endpoint discovery data") from err

    if not isinstance(endpoints, list):
        raise AmazonThermostatInvalidResponse("Endpoint discovery items must be a list")

    devices: list[AlexaThermostatDevice] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue

        endpoint_id = endpoint.get("id")
        name = endpoint.get("friendlyName")
        device_type = endpoint.get("displayCategories", {}).get("primary", {}).get("value")
        enabled = endpoint.get("enablement") == "ENABLED"
        if not isinstance(endpoint_id, str) or not isinstance(name, str):
            continue
        if not enabled or device_type != "THERMOSTAT":
            continue

        supported_operations: set[str] = set()
        for feature in endpoint.get("features") or []:
            if not isinstance(feature, dict):
                continue
            for operation in feature.get("operations") or []:
                operation_name = operation.get("name") if isinstance(operation, dict) else None
                if isinstance(operation_name, str):
                    supported_operations.add(operation_name)

        if "setTargetSetpoint" not in supported_operations:
            continue

        devices.append(
            AlexaThermostatDevice(
                endpoint_id=endpoint_id,
                device_id=_endpoint_device_id(endpoint_id),
                name=name,
                manufacturer=_text_value(endpoint.get("manufacturer")),
                model=_text_value(endpoint.get("model")),
                serial_number=_text_value(endpoint.get("serialNumber")),
                supported_operations=frozenset(supported_operations),
            )
        )

    return devices


def parse_thermostat_state_response(
    device: AlexaThermostatDevice, response: dict[str, Any]
) -> AlexaThermostatData:
    """Parse a thermostat state response."""
    try:
        features = response["data"]["endpoint"]["features"]
    except (KeyError, TypeError) as err:
        raise AmazonThermostatInvalidResponse("Missing thermostat state data") from err

    if not isinstance(features, list):
        raise AmazonThermostatInvalidResponse("Thermostat features must be a list")

    current_temperature: AlexaTemperature | None = None
    target_setpoint: AlexaTemperature | None = None
    lower_setpoint: AlexaTemperature | None = None
    upper_setpoint: AlexaTemperature | None = None
    thermostat_mode: ThermostatMode | None = None
    current_humidity: float | None = None

    for feature in features:
        if not isinstance(feature, dict):
            continue
        feature_name = feature.get("name")
        properties = feature.get("properties") or []
        if not isinstance(properties, list):
            continue

        for prop in properties:
            if not isinstance(prop, dict):
                continue
            property_name = prop.get("name")

            if feature_name == "temperatureSensor":
                current_temperature = _parse_temperature(prop.get("value")) or current_temperature
                continue

            if feature_name == "thermostat":
                if property_name == "thermostatMode":
                    raw_mode = prop.get("thermostatModeValue")
                    if raw_mode in {"HEAT", "COOL", "AUTO", "ECO", "OFF"}:
                        thermostat_mode = raw_mode  # type: ignore[assignment]
                elif property_name == "targetSetpoint":
                    target_setpoint = _parse_temperature(prop.get("value")) or target_setpoint
                elif property_name == "lowerSetpoint":
                    lower_setpoint = _parse_temperature(prop.get("value")) or lower_setpoint
                elif property_name == "upperSetpoint":
                    upper_setpoint = _parse_temperature(prop.get("value")) or upper_setpoint
                continue

            if feature_name == "range" and _range_name(feature) == HUMIDITY_RANGE_NAME:
                range_value = prop.get("rangeValue", {}).get("value")
                if isinstance(range_value, (int, float)):
                    current_humidity = float(range_value)

    return AlexaThermostatData(
        device=device,
        current_temperature=current_temperature,
        target_setpoint=target_setpoint,
        lower_setpoint=lower_setpoint,
        upper_setpoint=upper_setpoint,
        thermostat_mode=thermostat_mode,
        current_humidity=current_humidity,
    )


def _range_name(feature: dict[str, Any]) -> str | None:
    """Extract range feature friendly name."""
    name = (
        feature.get("configuration", {})
        .get("friendlyName", {})
        .get("value", {})
        .get("text")
    )
    return name if isinstance(name, str) else None

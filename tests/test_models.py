"""Tests for Alexa thermostat parsers."""

from __future__ import annotations

from custom_components.amazon_thermostat.models import (
    HUMIDITY_RANGE_NAME,
    parse_discovery_response,
    parse_thermostat_state_response,
)


def _endpoint(
    endpoint_id: str,
    *,
    category: str = "THERMOSTAT",
    enabled: bool = True,
    operations: list[str] | None = None,
) -> dict:
    return {
        "id": endpoint_id,
        "friendlyName": "Hallway Thermostat",
        "displayCategories": {"primary": {"value": category}},
        "serialNumber": {"value": {"text": "serial-1"}},
        "enablement": "ENABLED" if enabled else "DISABLED",
        "model": {"value": {"text": "Thermostat"}},
        "manufacturer": {"value": {"text": "Amazon"}},
        "features": [
            {
                "name": "thermostat",
                "operations": [{"name": name} for name in operations or ["setTargetSetpoint"]],
                "properties": [],
            }
        ],
    }


def test_parse_discovery_response_filters_supported_thermostats() -> None:
    """Only enabled thermostats with setTargetSetpoint are discovered."""
    response = {
        "data": {
            "endpoints": {
                "items": [
                    _endpoint("amzn1.alexa.endpoint.good"),
                    _endpoint("amzn1.alexa.endpoint.disabled", enabled=False),
                    _endpoint("amzn1.alexa.endpoint.light", category="LIGHT"),
                    _endpoint("amzn1.alexa.endpoint.no-setpoint", operations=["setThermostatMode"]),
                ]
            }
        }
    }

    devices = parse_discovery_response(response)

    assert len(devices) == 1
    assert devices[0].endpoint_id == "amzn1.alexa.endpoint.good"
    assert devices[0].device_id == "good"
    assert devices[0].name == "Hallway Thermostat"


def test_parse_thermostat_state_response_reads_heat_state_and_humidity() -> None:
    """Thermostat state includes current temp, mode, setpoint, and humidity."""
    device = parse_discovery_response(
        {"data": {"endpoints": {"items": [_endpoint("amzn1.alexa.endpoint.good")]}}}
    )[0]
    response = {
        "data": {
            "endpoint": {
                "features": [
                    {
                        "name": "temperatureSensor",
                        "properties": [
                            {"name": "temperature", "value": {"value": 70.5, "scale": "FAHRENHEIT"}}
                        ],
                    },
                    {
                        "name": "thermostat",
                        "properties": [
                            {"name": "thermostatMode", "thermostatModeValue": "HEAT"},
                            {"name": "targetSetpoint", "value": {"value": "72", "scale": "FAHRENHEIT"}},
                        ],
                    },
                    {
                        "name": "range",
                        "configuration": {
                            "friendlyName": {"value": {"text": HUMIDITY_RANGE_NAME}}
                        },
                        "properties": [{"name": "rangeValue", "rangeValue": {"value": 45}}],
                    },
                ]
            }
        }
    }

    data = parse_thermostat_state_response(device, response)

    assert data.current_temperature is not None
    assert data.current_temperature.value == 70.5
    assert data.thermostat_mode == "HEAT"
    assert data.target_setpoint is not None
    assert data.target_setpoint.value == 72
    assert data.current_humidity == 45


def test_parse_thermostat_state_response_reads_auto_range() -> None:
    """AUTO mode lower and upper setpoints are parsed separately."""
    device = parse_discovery_response(
        {"data": {"endpoints": {"items": [_endpoint("amzn1.alexa.endpoint.good")]}}}
    )[0]
    response = {
        "data": {
            "endpoint": {
                "features": [
                    {
                        "name": "thermostat",
                        "properties": [
                            {"name": "thermostatMode", "thermostatModeValue": "AUTO"},
                            {"name": "lowerSetpoint", "value": {"value": 68, "scale": "FAHRENHEIT"}},
                            {"name": "upperSetpoint", "value": {"value": 76, "scale": "FAHRENHEIT"}},
                        ],
                    }
                ]
            }
        }
    }

    data = parse_thermostat_state_response(device, response)

    assert data.thermostat_mode == "AUTO"
    assert data.lower_setpoint is not None
    assert data.lower_setpoint.value == 68
    assert data.upper_setpoint is not None
    assert data.upper_setpoint.value == 76

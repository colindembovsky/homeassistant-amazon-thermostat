"""Diagnostics for Amazon Thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIE_DATA
from .coordinator import AmazonThermostatConfigEntry

TO_REDACT = {
    CONF_COOKIE_DATA,
    "cookie",
    "cookieData",
    "cookie_data",
    "csrf",
    "deviceId",
    "deviceSerial",
    "email",
    "frc",
    "loginCookie",
    "localCookie",
    "map-md",
    "macDms",
    "mac_dms",
    "oauth",
    "password",
    "access_token",
    "authorization_code",
    "code_verifier",
    "refresh_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmazonThermostatConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with credentials redacted."""
    coordinator = entry.runtime_data
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": async_redact_data(entry.options, TO_REDACT),
        "devices": {
            endpoint_id: {
                "name": data.device.name,
                "manufacturer": data.device.manufacturer,
                "model": data.device.model,
                "thermostat_mode": data.thermostat_mode,
                "has_current_temperature": data.current_temperature is not None,
                "has_target_setpoint": data.target_setpoint is not None,
                "has_temperature_range": data.lower_setpoint is not None
                and data.upper_setpoint is not None,
                "has_humidity": data.current_humidity is not None,
            }
            for endpoint_id, data in coordinator.data.items()
        },
    }

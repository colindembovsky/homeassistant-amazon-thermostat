"""Alexa GraphQL API client for Amazon thermostats."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .auth import AlexaAuthSessionProvider
from .models import (
    AlexaThermostatData,
    AlexaThermostatDevice,
    AmazonThermostatApiError,
    AmazonThermostatAuthError,
    AmazonThermostatError,
    AmazonThermostatInvalidResponse,
    AmazonThermostatThrottleError,
    parse_discovery_response,
    parse_thermostat_state_response,
)

_LOGGER = logging.getLogger(__name__)

ENDPOINTS_QUERY = """
query Endpoints {
  endpoints {
    items {
      id
      friendlyName
      displayCategories { primary { value } }
      serialNumber { value { text } }
      enablement
      model { value { text } }
      manufacturer { value { text } }
      features {
        name
        instance
        operations { name }
        properties {
          name
          ... on RangeValue { rangeValue { value } }
          ... on TemperatureSensor { value { value scale } }
          ... on Setpoint { value { value scale } }
          ... on ThermostatMode { thermostatModeValue }
        }
        configuration {
          ... on RangeConfiguration {
            friendlyName { value { text } }
          }
        }
      }
    }
  }
}
"""

THERMOSTAT_QUERY = """
query getThermostatStates($endpointId: String!) {
  endpoint(id: $endpointId) {
    features {
      name
      properties {
        name
        ... on RangeValue { rangeValue { value } }
        ... on Setpoint { value { value scale } }
        ... on TemperatureSensor { value { value scale } }
        ... on ThermostatMode { thermostatModeValue }
      }
      configuration {
        ... on RangeConfiguration {
          friendlyName { value { text } }
        }
      }
    }
  }
}
"""

SET_ENDPOINT_FEATURES_MUTATION = """
mutation updatePowerFeatureForEndpoints($featureControlRequests: [FeatureControlRequest!]!) {
  setEndpointFeatures(
    setEndpointFeaturesInput: {
      featureControlRequests: $featureControlRequests
    }
  ) {
    featureControlResponses { endpointId featureOperationName __typename }
    errors { endpointId code __typename }
    __typename
  }
}
"""


class AlexaThermostatApi:
    """Minimal Alexa GraphQL client using an existing Amazon cookie."""

    def __init__(
        self,
        auth_provider: AlexaAuthSessionProvider,
    ) -> None:
        """Initialize the API client."""
        self._auth_provider = auth_provider
        self._semaphore = asyncio.Semaphore(2)

    @property
    def _base_url(self) -> str:
        """Return the regional Alexa service base URL."""
        return f"https://alexa.{self._auth_provider.amazon_domain}"

    async def async_validate_auth(self) -> None:
        """Validate auth by issuing discovery."""
        await self.async_discover_thermostats()

    async def async_discover_thermostats(self) -> list[AlexaThermostatDevice]:
        """Discover Alexa thermostat endpoints."""
        response = await self._post_graphql(ENDPOINTS_QUERY)
        return parse_discovery_response(response)

    async def async_get_thermostat_state(
        self, device: AlexaThermostatDevice
    ) -> AlexaThermostatData:
        """Fetch thermostat state for one endpoint."""
        response = await self._post_graphql(
            THERMOSTAT_QUERY, {"endpointId": device.endpoint_id}
        )
        return parse_thermostat_state_response(device, response)

    async def async_set_thermostat_mode(
        self, endpoint_id: str, mode: str
    ) -> None:
        """Set Alexa thermostat mode."""
        await self._set_endpoint_feature(
            endpoint_id,
            "thermostat",
            "setThermostatMode",
            {"thermostatMode": mode},
        )

    async def async_set_target_temperature(
        self, endpoint_id: str, temperature: float, scale: str
    ) -> None:
        """Set a single target temperature."""
        await self._set_endpoint_feature(
            endpoint_id,
            "thermostat",
            "setTargetSetpoint",
            {"targetSetpoint": {"value": _format_temperature(temperature), "scale": scale}},
        )

    async def async_set_temperature_range(
        self, endpoint_id: str, low: float, high: float, scale: str
    ) -> None:
        """Set heat/cool range targets in one Alexa mutation."""
        await self._set_endpoint_feature(
            endpoint_id,
            "thermostat",
            "setTargetSetpoint",
            {
                "lowerSetpoint": {"value": _format_temperature(low), "scale": scale},
                "upperSetpoint": {"value": _format_temperature(high), "scale": scale},
            },
        )

    async def async_turn_on(self, endpoint_id: str) -> None:
        """Turn on the thermostat endpoint."""
        await self._set_endpoint_feature(endpoint_id, "thermostat", "turnOn")

    async def async_turn_off(self, endpoint_id: str) -> None:
        """Turn off the thermostat endpoint."""
        await self._set_endpoint_feature(endpoint_id, "thermostat", "turnOff")

    async def _set_endpoint_feature(
        self,
        endpoint_id: str,
        feature_name: str,
        operation_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Send a setEndpointFeatures mutation and validate the response."""
        request: dict[str, Any] = {
            "endpointId": endpoint_id,
            "featureName": feature_name,
            "featureOperationName": operation_name,
        }
        if payload:
            request["payload"] = payload

        response = await self._post_graphql(
            SET_ENDPOINT_FEATURES_MUTATION,
            {"featureControlRequests": [request]},
        )
        errors = (
            response.get("data", {})
            .get("setEndpointFeatures", {})
            .get("errors", [])
        )
        if errors:
            raise AmazonThermostatApiError(f"Alexa rejected {operation_name}: {errors}")

    async def _post_graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Post a GraphQL request to Alexa."""
        async with self._semaphore:
            data = await self._post_graphql_once(query, variables)

        if not isinstance(data, dict):
            raise AmazonThermostatInvalidResponse("Amazon GraphQL response must be an object")
        if "errors" in data:
            raise AmazonThermostatApiError(f"Alexa GraphQL errors: {data['errors']}")
        return data

    async def _post_graphql_once(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Post one GraphQL request, refreshing auth once on 401/403."""
        try:
            return await self._post_graphql_raw(query, variables)
        except AmazonThermostatAuthError:
            if await self._auth_provider.async_refresh():
                return await self._post_graphql_raw(query, variables)
            raise

    async def _post_graphql_raw(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Post one raw GraphQL request."""
        try:
            session = await self._auth_provider.async_get_session()
            response = await session.post(
                f"{self._base_url}/nexus/v1/graphql",
                headers=await self._auth_provider.async_get_headers(),
                json={"query": query, "variables": variables or {}},
            )
            if response.status in (401, 403):
                raise AmazonThermostatAuthError("Amazon authentication failed")
            if response.status == 429:
                raise AmazonThermostatThrottleError("Amazon rate limit exceeded")
            response.raise_for_status()
            return await response.json(content_type=None)
        except AmazonThermostatError:
            raise
        except ClientResponseError as err:
            if err.status in (500, 502, 503, 504):
                raise AmazonThermostatThrottleError("Amazon service unavailable") from err
            raise AmazonThermostatApiError(f"Amazon API HTTP error: {err.status}") from err
        except (ClientError, TimeoutError) as err:
            raise AmazonThermostatApiError("Could not communicate with Amazon") from err


def _format_temperature(value: float) -> str:
    """Format temperatures the way the upstream GraphQL mutation does."""
    if float(value).is_integer():
        return str(int(value))
    return str(value)

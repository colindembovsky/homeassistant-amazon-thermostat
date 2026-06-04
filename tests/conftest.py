"""Test package setup for pure unit tests without Home Assistant installed."""

from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
INTEGRATION = CUSTOM_COMPONENTS / "amazon_thermostat"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(CUSTOM_COMPONENTS)]

amazon_thermostat = types.ModuleType("custom_components.amazon_thermostat")
amazon_thermostat.__path__ = [str(INTEGRATION)]

sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.amazon_thermostat", amazon_thermostat)

"""Shared test fixtures for Control4 Dimmers tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.store import Control4Store

# ── Fixtures: mock Home Assistant core ────────────────────────────────


@pytest.fixture
def mock_hass() -> MagicMock:
    """Return a lightweight mock HomeAssistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.states = MagicMock()
    hass.config_entries = MagicMock()
    hass.bus = MagicMock()
    hass.state = MagicMock()
    hass.services = MagicMock()
    return hass


@pytest.fixture
def mock_entry() -> MagicMock:
    """Return a mock ConfigEntry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.title = "Control4 Dimmers"
    entry.data = {"mqtt_topic": "zigbee2mqtt"}
    entry.options = {}
    return entry


@pytest.fixture
def mock_store(mock_hass: MagicMock, mock_entry: MagicMock) -> Control4Store:
    """Return a Control4Store backed by a fake HA Store."""
    with patch("custom_components.control4_dimmers.store.Store"):
        store = Control4Store(mock_hass, mock_entry.entry_id)
    return store


@pytest.fixture
def manager(
    mock_hass: MagicMock,
    mock_entry: MagicMock,
    mock_store: Control4Store,
) -> Control4Manager:
    """Return a Control4Manager with mocked MQTT."""
    mgr = Control4Manager(mock_hass, mock_entry, mock_store)
    return mgr


# ── Fixtures: test data ──────────────────────────────────────────────

IEEE_DIMMER = "0x000fff0000aaa001"
IEEE_KEYPAD = "0x000fff0000ccc001"


@pytest.fixture
def dimmer_state() -> DeviceState:
    """Return a typical dimmer DeviceState."""
    return DeviceState(
        ieee_address=IEEE_DIMMER,
        friendly_name="Kitchen",
        model_id="C4-APD120",
        device_type="dimmer",
        brightness=200,
        state="ON",
    )


@pytest.fixture
def keypad_state() -> DeviceState:
    """Return a typical keypad DeviceState."""
    return DeviceState(
        ieee_address=IEEE_KEYPAD,
        friendly_name="Theater",
        model_id="C4-KC120277",
        device_type="keypad",
    )


@pytest.fixture
def dimmer_config() -> DeviceConfig:
    """Return a typical dimmer DeviceConfig with load-control buttons."""
    return DeviceConfig(
        ieee_address=IEEE_DIMMER,
        friendly_name="Kitchen",
        device_type="dimmer",
        slots=[
            SlotConfig(
                slot_id=2,
                name="Top",
                behavior="load_on",
                led_mode="follow_load",
                led_on_color="ffffff",
                led_off_color="000000",
            ),
            SlotConfig(
                slot_id=5,
                name="Bottom",
                behavior="load_off",
                led_mode="follow_load",
                led_on_color="000000",
                led_off_color="0000ff",
            ),
        ],
    )


@pytest.fixture
def keypad_config() -> DeviceConfig:
    """Return a typical keypad DeviceConfig with 6 programmable slots."""
    return DeviceConfig(
        ieee_address=IEEE_KEYPAD,
        friendly_name="Theater",
        device_type="keypad",
        slots=[
            SlotConfig(
                slot_id=i, name=f"Button {i}", behavior="keypad", led_mode="fixed"
            )
            for i in range(1, 7)
        ],
    )


def make_bridge_device(
    *,
    ieee: str = IEEE_DIMMER,
    friendly_name: str = "Kitchen",
    vendor: str = "Control4",
    manufacturer: str = "Control4",
    model: str = "C4-Zigbee",
) -> dict[str, Any]:
    """Build a single Z2M bridge/devices entry matching real Z2M output."""
    return {
        "ieee_address": ieee,
        "friendly_name": friendly_name,
        "manufacturer": manufacturer,
        "type": "Router",
        "supported": True,
        "definition": {
            "model": model,
            "vendor": vendor,
        },
    }

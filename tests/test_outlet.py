"""Tests for the outlet relay (C4-V-ROSW120) button support."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.control4_dimmers.const import DEVICE_TYPE_OUTLET, DOMAIN
from custom_components.control4_dimmers.event import (
    Control4ButtonEvent,
    Control4PaddleEvent,
)
from custom_components.control4_dimmers.event import (
    async_setup_entry as event_async_setup_entry,
)
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.sensor import (
    Control4AmbientLightSensor,
    Control4DeviceSensor,
)
from custom_components.control4_dimmers.sensor import (
    async_setup_entry as sensor_async_setup_entry,
)

IEEE_OUTLET = "0x000fff0000d26472"


def _outlet_bridge_device(**overrides: Any) -> dict[str, Any]:
    """Build the bridge/devices entry Z2M publishes for a ROS."""
    device = {
        "ieee_address": IEEE_OUTLET,
        "friendly_name": "Mailbox",
        "manufacturer": "Control4",
        "model_id": "c4:control4_light:C4-V-ROSW120",
        "type": "Router",
        "supported": True,
        "definition": {"model": "C4-V-ROSW120", "vendor": "Control4"},
    }
    device.update(overrides)
    return device


def _bridge_msg(devices: list[dict[str, Any]]) -> MagicMock:
    msg = MagicMock()
    msg.topic = "zigbee2mqtt/bridge/devices"
    msg.payload = json.dumps(devices)
    return msg


class TestOutletDiscovery:
    """The ROS gets the outlet type from its model."""

    @pytest.mark.asyncio
    async def test_outlet_type_from_definition_model(
        self, manager: Control4Manager
    ) -> None:
        await manager._handle_bridge_devices(_bridge_msg([_outlet_bridge_device()]))
        assert manager.devices[IEEE_OUTLET].device_type == DEVICE_TYPE_OUTLET

    @pytest.mark.asyncio
    async def test_outlet_type_from_raw_model_id(
        self, manager: Control4Manager
    ) -> None:
        # Without a definition, the raw Zigbee modelID still identifies it.
        device = _outlet_bridge_device(definition=None)
        await manager._handle_bridge_devices(_bridge_msg([device]))
        assert manager.devices[IEEE_OUTLET].device_type == DEVICE_TYPE_OUTLET

    @pytest.mark.asyncio
    async def test_outlet_is_never_sent_detect(self, manager: Control4Manager) -> None:
        await manager._handle_bridge_devices(_bridge_msg([_outlet_bridge_device()]))
        manager._maybe_auto_detect(manager.devices[IEEE_OUTLET])
        assert IEEE_OUTLET not in manager._detect_sent


class TestOutletEntities:
    """The outlet gets one button event entity and no ambient sensor."""

    @pytest.mark.asyncio
    async def test_one_button_event_no_paddles(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
    ) -> None:
        await manager._handle_bridge_devices(_bridge_msg([_outlet_bridge_device()]))
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
        added: list = []
        await event_async_setup_entry(mock_hass, mock_entry, added.extend)
        assert {e.unique_id for e in added} == {f"{IEEE_OUTLET}_event_1"}
        assert isinstance(added[0], Control4ButtonEvent)
        assert not any(isinstance(e, Control4PaddleEvent) for e in added)

    @pytest.mark.asyncio
    async def test_anchor_sensor_but_no_ambient_light(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
    ) -> None:
        await manager._handle_bridge_devices(_bridge_msg([_outlet_bridge_device()]))
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
        added: list = []
        await sensor_async_setup_entry(mock_hass, mock_entry, added.extend)
        assert any(isinstance(e, Control4DeviceSensor) for e in added)
        assert not any(isinstance(e, Control4AmbientLightSensor) for e in added)

    @pytest.mark.asyncio
    async def test_button_press_fires_event(self, manager: Control4Manager) -> None:
        await manager._handle_bridge_devices(_bridge_msg([_outlet_bridge_device()]))
        cb = MagicMock()
        manager.register_event_callback(IEEE_OUTLET, 1, cb)
        manager._dispatch_button_action(
            manager.devices[IEEE_OUTLET], "button_1_click_1"
        )
        cb.assert_called_once_with("single_tap")

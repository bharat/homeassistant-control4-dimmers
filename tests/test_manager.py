"""Tests for Control4Manager device discovery, state, and dispatch."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.control4_dimmers.manager import (
    C4_MODEL_IDS,
    Control4Manager,
    _click_count_to_event_type,
    _is_control4_device,
)
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
)
from custom_components.control4_dimmers.store import Control4Store

from .conftest import IEEE_DIMMER, IEEE_KEYPAD, make_bridge_device

# ── Helpers ──────────────────────────────────────────────────────────


def _make_mqtt_msg(topic: str, payload: Any) -> MagicMock:
    """Create a fake MQTT ReceiveMessage."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = json.dumps(payload) if isinstance(payload, (dict, list)) else payload
    return msg


# ── _is_control4_device ─────────────────────────────────────────────


class TestIsControl4Device:
    """Tests for the _is_control4_device helper."""

    def test_recognises_by_manufacturer(self) -> None:
        info = {"definition": {"manufacturer": "Control4", "model": "CustomModel"}}
        assert _is_control4_device(info) is True

    def test_recognises_by_model(self) -> None:
        for model in C4_MODEL_IDS:
            info = {"definition": {"manufacturer": "", "model": model}}
            assert _is_control4_device(info)

    def test_recognises_by_model_id(self) -> None:
        for model_id in C4_MODEL_IDS:
            info = {"model_id": model_id, "definition": {}}
            assert _is_control4_device(info) is True

    def test_rejects_non_c4(self) -> None:
        info = {
            "model_id": "TRADFRI",
            "definition": {"manufacturer": "IKEA", "model": "E1766"},
        }
        assert _is_control4_device(info) is False

    def test_handles_missing_definition(self) -> None:
        assert _is_control4_device({}) is False
        assert _is_control4_device({"definition": None}) is False


# ── _click_count_to_event_type ───────────────────────────────────────


class TestClickCountToEventType:
    """Tests for click-count-to-event mapping."""

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (1, "press"),
            (2, "double_press"),
            (3, "triple_press"),
            (4, "quadruple_press"),
            (5, "click_5"),
            (99, "click_99"),
        ],
    )
    def test_mapping(self, count: int, expected: str) -> None:
        assert _click_count_to_event_type(count) == expected


# ── Manager: properties & helpers ────────────────────────────────────


class TestManagerProperties:
    """Tests for basic manager properties."""

    def test_mqtt_topic_from_data(self, manager: Control4Manager) -> None:
        assert manager.mqtt_topic == "zigbee2mqtt"

    def test_mqtt_topic_from_options(
        self, mock_hass: MagicMock, mock_entry: MagicMock, mock_store: Control4Store
    ) -> None:
        mock_entry.options = {"mqtt_topic": "z2m_custom"}
        mgr = Control4Manager(mock_hass, mock_entry, mock_store)
        assert mgr.mqtt_topic == "z2m_custom"

    def test_devices_empty_initially(self, manager: Control4Manager) -> None:
        assert manager.devices == {}

    def test_store_property(
        self, manager: Control4Manager, mock_store: Control4Store
    ) -> None:
        assert manager.store is mock_store


# ── Manager: listener pattern ────────────────────────────────────────


class TestManagerListeners:
    """Tests for the add_listener / _notify_listeners pattern."""

    def test_add_and_notify_listener(self, manager: Control4Manager) -> None:
        callback = MagicMock()
        manager.add_listener(callback)
        manager._notify_listeners()
        callback.assert_called_once()

    def test_unsubscribe_listener(self, manager: Control4Manager) -> None:
        callback = MagicMock()
        unsub = manager.add_listener(callback)
        unsub()
        manager._notify_listeners()
        callback.assert_not_called()

    def test_multiple_listeners(self, manager: Control4Manager) -> None:
        cb1, cb2 = MagicMock(), MagicMock()
        manager.add_listener(cb1)
        manager.add_listener(cb2)
        manager._notify_listeners()
        cb1.assert_called_once()
        cb2.assert_called_once()


# ── Manager: event callbacks ─────────────────────────────────────────


class TestManagerEventCallbacks:
    """Tests for register_event_callback and dispatch."""

    def test_register_and_fire(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 1, cb)
        manager._dispatch_button_action(dimmer_state, "button_1_press")
        cb.assert_called_once_with("press")

    def test_click_action_dispatch(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 3, cb)
        manager._dispatch_button_action(dimmer_state, "button_3_click_2")
        cb.assert_called_once_with("double_press")

    def test_unsubscribe_event_callback(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        unsub = manager.register_event_callback(IEEE_DIMMER, 1, cb)
        unsub()
        manager._dispatch_button_action(dimmer_state, "button_1_press")
        cb.assert_not_called()

    def test_empty_action_ignored(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 0, cb)
        manager._dispatch_button_action(dimmer_state, "")
        cb.assert_not_called()


# ── Manager: bridge device handling ──────────────────────────────────


class TestManagerBridgeDevices:
    """Tests for _handle_bridge_devices."""

    @pytest.mark.asyncio
    async def test_discovers_c4_device(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device()],
        )
        await manager._handle_bridge_devices(msg)
        assert IEEE_DIMMER in manager.devices
        assert manager.devices[IEEE_DIMMER].friendly_name == "Kitchen"

    @pytest.mark.asyncio
    async def test_ignores_non_c4_device(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [
                {
                    "ieee_address": "0xIKEA",
                    "friendly_name": "Lamp",
                    "model_id": "TRADFRI",
                    "definition": {"manufacturer": "IKEA", "model": "E1766"},
                }
            ],
        )
        await manager._handle_bridge_devices(msg)
        assert len(manager.devices) == 0

    @pytest.mark.asyncio
    async def test_removes_vanished_device(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device()],
        )
        await manager._handle_bridge_devices(msg)
        assert IEEE_DIMMER in manager.devices

        msg2 = _make_mqtt_msg("zigbee2mqtt/bridge/devices", [])
        await manager._handle_bridge_devices(msg2)
        assert IEEE_DIMMER not in manager.devices

    @pytest.mark.asyncio
    async def test_updates_friendly_name(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device(friendly_name="Kitchen")],
        )
        await manager._handle_bridge_devices(msg)

        msg2 = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device(friendly_name="Kitchen Updated")],
        )
        await manager._handle_bridge_devices(msg2)
        assert manager.devices[IEEE_DIMMER].friendly_name == "Kitchen Updated"

    @pytest.mark.asyncio
    async def test_applies_buffered_state(self, manager: Control4Manager) -> None:
        state_msg = _make_mqtt_msg(
            "zigbee2mqtt/Kitchen",
            {"state": "ON", "brightness": 200},
        )
        await manager._handle_device_state(state_msg)

        bridge_msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device(friendly_name="Kitchen")],
        )
        await manager._handle_bridge_devices(bridge_msg)
        assert manager.devices[IEEE_DIMMER].state == "ON"
        assert manager.devices[IEEE_DIMMER].brightness == 200

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self, manager: Control4Manager) -> None:
        msg = MagicMock()
        msg.payload = "not json"
        msg.topic = "zigbee2mqtt/bridge/devices"
        await manager._handle_bridge_devices(msg)
        assert len(manager.devices) == 0

    @pytest.mark.asyncio
    async def test_rejects_non_list(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg("zigbee2mqtt/bridge/devices", {"not": "a list"})
        await manager._handle_bridge_devices(msg)
        assert len(manager.devices) == 0

    @pytest.mark.asyncio
    async def test_notifies_listeners(self, manager: Control4Manager) -> None:
        listener = MagicMock()
        manager.add_listener(listener)
        msg = _make_mqtt_msg(
            "zigbee2mqtt/bridge/devices",
            [make_bridge_device()],
        )
        await manager._handle_bridge_devices(msg)
        listener.assert_called()


# ── Manager: device state handling ───────────────────────────────────


class TestManagerDeviceState:
    """Tests for _handle_device_state."""

    @pytest.mark.asyncio
    async def test_updates_known_device(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        msg = _make_mqtt_msg("zigbee2mqtt/Kitchen", {"brightness": 50})
        await manager._handle_device_state(msg)
        assert dimmer_state.brightness == 50

    @pytest.mark.asyncio
    async def test_buffers_unknown_device(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg("zigbee2mqtt/Unknown", {"state": "ON"})
        await manager._handle_device_state(msg)
        assert "Unknown" in manager._pending_states

    @pytest.mark.asyncio
    async def test_dispatches_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 1, cb)
        msg = _make_mqtt_msg(
            "zigbee2mqtt/Kitchen",
            {"action": "button_1_press"},
        )
        await manager._handle_device_state(msg)
        cb.assert_called_once_with("press")

    @pytest.mark.asyncio
    async def test_ignores_bridge_subtopics(self, manager: Control4Manager) -> None:
        msg = _make_mqtt_msg("zigbee2mqtt/bridge/config", {"log_level": "debug"})
        await manager._handle_device_state(msg)
        assert len(manager._pending_states) == 0


# ── Manager: get_device_info / get_all_devices_info ──────────────────


class TestManagerDeviceInfo:
    """Tests for device info getters."""

    def test_get_device_info(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
        dimmer_config: DeviceConfig,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._store._devices[IEEE_DIMMER] = dimmer_config
        info = manager.get_device_info(IEEE_DIMMER)
        assert info is not None
        assert info["ieee_address"] == IEEE_DIMMER
        assert info["friendly_name"] == "Kitchen"
        assert info["config"] is not None

    def test_get_device_info_unknown(self, manager: Control4Manager) -> None:
        assert manager.get_device_info("0xNONE") is None

    def test_get_all_devices_info(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
        keypad_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._devices[IEEE_KEYPAD] = keypad_state
        all_info = manager.get_all_devices_info()
        assert len(all_info) == 2
        iees = {d["ieee_address"] for d in all_info}
        assert IEEE_DIMMER in iees
        assert IEEE_KEYPAD in iees


# ── Manager: default slots ───────────────────────────────────────────


class TestManagerDefaultSlots:
    """Tests for get_default_slots."""

    def test_dimmer_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("dimmer")
        assert len(slots) == 2
        ids = [s.slot_id for s in slots]
        assert 1 in ids
        assert 4 in ids

    def test_keypad_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypad")
        assert len(slots) == 6
        assert slots[0].slot_id == 0
        assert slots[5].slot_id == 5

    def test_keypaddim_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypaddim")
        assert len(slots) == 6


# ── Manager: configure_device ────────────────────────────────────────


class TestManagerConfigureDevice:
    """Tests for async_configure_device."""

    @pytest.mark.asyncio
    async def test_configure_saves_to_store(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._store.async_save_device = AsyncMock()
        await manager.async_configure_device(
            ieee_address=IEEE_DIMMER,
            device_type_override="keypaddim",
        )
        manager._store.async_save_device.assert_awaited_once()
        saved = manager._store.async_save_device.call_args[0][0]
        assert saved.device_type_override == "keypaddim"

    @pytest.mark.asyncio
    async def test_configure_unknown_device(self, manager: Control4Manager) -> None:
        manager._store.async_save_device = AsyncMock()
        await manager.async_configure_device(
            ieee_address="0xNONE",
            device_type_override="dimmer",
        )
        manager._store.async_save_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_configure_notifies_listeners(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._store.async_save_device = AsyncMock()
        listener = MagicMock()
        manager.add_listener(listener)
        await manager.async_configure_device(
            ieee_address=IEEE_DIMMER,
            device_type_override="keypad",
        )
        listener.assert_called()

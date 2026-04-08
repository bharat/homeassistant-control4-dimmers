"""Tests for Control4Manager device discovery, state, and dispatch."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    SlotConfig,
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

    def test_recognises_by_definition_vendor(self) -> None:
        """Real Z2M uses definition.vendor, not definition.manufacturer."""
        info = {"definition": {"vendor": "Control4", "model": "C4-Zigbee"}}
        assert _is_control4_device(info) is True

    def test_recognises_by_definition_manufacturer(self) -> None:
        """Legacy/fallback: check definition.manufacturer too."""
        info = {"definition": {"manufacturer": "Control4", "model": "CustomModel"}}
        assert _is_control4_device(info) is True

    def test_recognises_by_top_level_manufacturer(self) -> None:
        """Real Z2M puts manufacturer at the top level."""
        info = {"manufacturer": "Control4", "definition": {}}
        assert _is_control4_device(info) is True

    def test_recognises_by_definition_model(self) -> None:
        for model in C4_MODEL_IDS:
            info = {"definition": {"vendor": "", "model": model}}
            assert _is_control4_device(info)

    def test_recognises_by_model_id(self) -> None:
        for model_id in C4_MODEL_IDS:
            info = {"model_id": model_id, "definition": {}}
            assert _is_control4_device(info) is True

    def test_rejects_non_c4(self) -> None:
        info = {
            "model_id": "TRADFRI",
            "manufacturer": "IKEA",
            "definition": {"vendor": "IKEA", "model": "E1766"},
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
            (1, "single_tap"),
            (2, "double_tap"),
            (3, "triple_tap"),
            (4, "click_4"),
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
    """Tests for the add_listener / notify_listeners pattern."""

    def test_add_and_notify_listener(self, manager: Control4Manager) -> None:
        callback = MagicMock()
        manager.add_listener(callback)
        manager.notify_listeners()
        callback.assert_called_once()

    def test_unsubscribe_listener(self, manager: Control4Manager) -> None:
        callback = MagicMock()
        unsub = manager.add_listener(callback)
        unsub()
        manager.notify_listeners()
        callback.assert_not_called()

    def test_multiple_listeners(self, manager: Control4Manager) -> None:
        cb1, cb2 = MagicMock(), MagicMock()
        manager.add_listener(cb1)
        manager.add_listener(cb2)
        manager.notify_listeners()
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
        manager.register_event_callback(IEEE_DIMMER, 2, cb)
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        cb.assert_called_once_with("pressed")

    def test_release_action_dispatch(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 2, cb)
        manager._dispatch_button_action(dimmer_state, "button_2_release")
        cb.assert_called_once_with("released")

    def test_click_action_dispatch(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 3, cb)
        manager._dispatch_button_action(dimmer_state, "button_3_click_2")
        cb.assert_called_once_with("double_tap")

    def test_unsubscribe_event_callback(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        cb = MagicMock()
        unsub = manager.register_event_callback(IEEE_DIMMER, 2, cb)
        unsub()
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        cb.assert_not_called()

    def test_empty_action_ignored(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        cb = MagicMock()
        manager.register_event_callback(IEEE_DIMMER, 1, cb)
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
        with patch(
            "custom_components.control4_dimmers.manager.dr.async_get"
        ) as mock_dr:
            mock_registry = MagicMock()
            mock_registry.async_get_device.return_value = None
            mock_dr.return_value = mock_registry
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
        manager.register_event_callback(IEEE_DIMMER, 2, cb)
        msg = _make_mqtt_msg(
            "zigbee2mqtt/Kitchen",
            {"action": "button_2_press"},
        )
        await manager._handle_device_state(msg)
        cb.assert_called_once_with("pressed")

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
        top = next(s for s in slots if s.slot_id == 2)
        assert top.behavior == "load_on"
        assert top.led_mode == "follow_load"
        assert top.tap_action is None
        bottom = next(s for s in slots if s.slot_id == 5)
        assert bottom.behavior == "load_off"

    def test_keypad_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypad")
        assert len(slots) == 6
        assert all(s.behavior == "keypad" for s in slots)
        assert all(s.tap_action is None for s in slots)

    def test_keypaddim_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypaddim")
        assert len(slots) == 6
        assert slots[0].behavior == "toggle_load"
        assert slots[0].led_mode == "follow_load"
        assert slots[0].tap_action is None
        assert slots[1].behavior == "keypad"


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


# ── Manager: _execute_slot_action ──────────────────────────────────


class TestExecuteSlotAction:
    """Tests for execute_slot_action with HA-native action format."""

    @pytest.mark.asyncio
    async def test_null_action_does_nothing(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, tap_action=None)],
        )
        manager._store._devices[IEEE_DIMMER] = config
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_light_toggle_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.kitchen"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "toggle", {"entity_id": "light.kitchen"}
        )

    @pytest.mark.asyncio
    async def test_light_turn_on_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action={
                        "action": "light.turn_on",
                        "target": {"entity_id": "light.kitchen"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.kitchen"}
        )

    @pytest.mark.asyncio
    async def test_self_load_resolution(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "__self_load__"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        light_state = MagicMock()
        light_state.entity_id = "light.kitchen"
        light_state.attributes = {"friendly_name": "Kitchen"}
        manager._hass.states.async_all.return_value = [light_state]
        manager._hass.services.async_call = AsyncMock()
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "toggle", {"entity_id": "light.kitchen"}
        )

    @pytest.mark.asyncio
    async def test_double_tap_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action=None,
                    double_tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.bedroom"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.execute_slot_action(IEEE_DIMMER, 2, "double_tap")
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "toggle", {"entity_id": "light.bedroom"}
        )

    @pytest.mark.asyncio
    async def test_action_without_target_entity(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Actions like scripts don't need a target entity."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    led_mode="fixed",
                    double_tap_action={"action": "script.activity_home"},
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.execute_slot_action(IEEE_DIMMER, 2, "double_tap")
        manager._hass.services.async_call.assert_awaited_once_with(
            "script", "activity_home", {}
        )

    @pytest.mark.asyncio
    async def test_no_config_does_nothing(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_not_called()


# ── Manager: dispatch with actions ──────────────────────────────────


class TestDispatchWithActions:
    """Tests for _dispatch_button_action dispatching to press_button."""

    def test_press_executes_tap_immediately_when_no_double_tap(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Press executes tap_action immediately without double_tap."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="keypad",
                    led_mode="fixed",
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.kitchen"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        # Should have created a task for press_button
        manager._hass.async_create_task.assert_called()

    def test_press_load_control_skips_software_toggle(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """
        Physical press on load-control button does NOT software-toggle.

        The C4 firmware handles load control directly at the hardware
        level. Our software only fires the event entity.
        """
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(slot_id=2, behavior="toggle_load", led_mode="follow_load")
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        # Should NOT create a task — firmware handles load control
        manager._hass.async_create_task.assert_not_called()

    def test_scene_event_triggers_load_control(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Scene event (c4.dmx.sc) on load-control button triggers software."""
        for behavior in ("toggle_load", "load_on", "load_off"):
            manager._hass.reset_mock()
            manager._devices[IEEE_DIMMER] = dimmer_state
            config = DeviceConfig(
                ieee_address=IEEE_DIMMER,
                friendly_name="Kitchen",
                device_type="dimmer",
                slots=[
                    SlotConfig(slot_id=1, behavior=behavior, led_mode="follow_load")
                ],
            )
            manager._store._devices[IEEE_DIMMER] = config
            manager._dispatch_button_action(dimmer_state, "button_1_scene")
            (
                manager._hass.async_create_task.assert_called(),
                (f"Scene on {behavior} should dispatch"),
            )

    def test_press_defers_when_double_tap_configured(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Press defers tap when double_tap is configured."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.kitchen"},
                    },
                    double_tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.x"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        # Should NOT have created a task for the action (only event was fired)
        manager._hass.async_create_task.assert_not_called()


# ── Manager: setup_light_tracking with led_track_entity_id ──────────


class TestSetupLightTrackingActions:
    """Tests for setup_light_tracking using led_track_entity_id."""

    def test_tracks_led_entity(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.kitchen"},
                    },
                    led_track_entity_id="light.kitchen",
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager.setup_light_tracking()
        # Should have registered a state listener
        manager._hass.bus.async_listen.assert_called()

    def test_no_tracking_without_led_track(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, tap_action=None, led_mode="fixed")],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager.setup_light_tracking()
        manager._hass.bus.async_listen.assert_not_called()


# ── Comprehensive LED + button behavior ──────────────────────────────


class TestLoadControlBehavior:
    """Load-control buttons use firmware behavior, not software actions."""

    def test_dimmer_defaults_are_load_control(self, manager: Control4Manager) -> None:
        """Dimmer Top=load_on, Bottom=load_off, both follow_load."""
        slots = manager.get_default_slots("dimmer")
        top = next(s for s in slots if s.slot_id == 2)
        bottom = next(s for s in slots if s.slot_id == 5)
        assert top.behavior == "load_on"
        assert top.led_mode == "follow_load"
        assert top.tap_action is None
        assert bottom.behavior == "load_off"
        assert bottom.led_mode == "follow_load"
        assert bottom.tap_action is None

    def test_keypaddim_button1_is_load_control(self, manager: Control4Manager) -> None:
        """Keypad-dimmer button 1 = toggle_load, rest = keypad."""
        slots = manager.get_default_slots("keypaddim")
        assert slots[0].behavior == "toggle_load"
        assert slots[0].led_mode == "follow_load"
        assert slots[0].tap_action is None
        assert slots[1].behavior == "keypad"

    def test_keypad_all_programmable(self, manager: Control4Manager) -> None:
        """Pure keypad has no load-control buttons."""
        slots = manager.get_default_slots("keypad")
        assert all(s.behavior == "keypad" for s in slots)

    @pytest.mark.asyncio
    async def test_press_load_on_calls_light_turn_on(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """press_button with load_on behavior calls light.turn_on."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, behavior="load_on", led_mode="follow_load")],
        )
        manager._store._devices[IEEE_DIMMER] = config
        # Mock the light entity lookup
        light_state = MagicMock()
        light_state.entity_id = "light.kitchen"
        light_state.attributes = {"friendly_name": "Kitchen"}
        manager._hass.states.async_all.return_value = [light_state]
        manager._hass.services.async_call = AsyncMock()
        await manager.press_button(IEEE_DIMMER, 2)
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.kitchen"}
        )

    @pytest.mark.asyncio
    async def test_press_toggle_load_calls_light_toggle(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """press_button with toggle_load calls light.toggle."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(slot_id=2, behavior="toggle_load", led_mode="follow_load")
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        light_state = MagicMock()
        light_state.entity_id = "light.kitchen"
        light_state.attributes = {"friendly_name": "Kitchen"}
        manager._hass.states.async_all.return_value = [light_state]
        manager._hass.services.async_call = AsyncMock()
        await manager.press_button(IEEE_DIMMER, 2)
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "toggle", {"entity_id": "light.kitchen"}
        )

    @pytest.mark.asyncio
    async def test_press_programmable_calls_execute_slot_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """press_button with keypad behavior executes tap_action."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="keypad",
                    led_mode="fixed",
                    tap_action={
                        "action": "light.toggle",
                        "target": {"entity_id": "light.bedroom"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.press_button(IEEE_DIMMER, 2)
        manager._hass.services.async_call.assert_awaited_once_with(
            "light", "toggle", {"entity_id": "light.bedroom"}
        )

    @pytest.mark.asyncio
    async def test_press_programmable_no_action_does_nothing(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """press_button with keypad behavior and no tap_action is a no-op."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, behavior="keypad", led_mode="fixed")],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._hass.services.async_call = AsyncMock()
        await manager.press_button(IEEE_DIMMER, 2)
        manager._hass.services.async_call.assert_not_called()


class TestPushSlotConfig:
    """Verify _push_slot_config sends correct firmware values."""

    @pytest.mark.asyncio
    async def test_load_control_sends_behavior_and_follow_load(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Load-control slot sends behavior=load_on, led_mode=follow_load."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="load_on",
                    led_mode="follow_load",
                    led_on_color="ffffff",
                    led_off_color="000000",
                )
            ],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        # Find the call that sends behavior/led_mode
        behavior_calls = [
            c for c in mock_mqtt.call_args_list if "button_2_behavior" in str(c)
        ]
        assert len(behavior_calls) == 1
        payload = behavior_calls[0][0][1]
        assert payload["button_2_behavior"] == "load_on"
        assert payload["button_2_led_mode"] == "follow_load"

    @pytest.mark.asyncio
    async def test_fixed_mode_sends_programmed_to_firmware(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Fixed LED mode maps to firmware 'programmed'."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="keypad",
                    led_mode="fixed",
                    led_on_color="0000ff",
                    led_off_color="000000",
                )
            ],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        behavior_calls = [
            c for c in mock_mqtt.call_args_list if "button_2_led_mode" in str(c)
        ]
        assert len(behavior_calls) == 1
        payload = behavior_calls[0][0][1]
        assert payload["button_2_led_mode"] == "programmed"

    @pytest.mark.asyncio
    async def test_sends_c4_dmx_btn_for_toggle_load(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """toggle_load sends c4.dmx.btn with firmware value 02."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(slot_id=2, behavior="toggle_load", led_mode="follow_load")
            ],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        btn_calls = [c for c in mock_mqtt.call_args_list if "c4.dmx.btn" in str(c)]
        assert len(btn_calls) == 1
        assert btn_calls[0][0][1] == {"c4_cmd": "c4.dmx.btn 01 01 02"}

    @pytest.mark.asyncio
    async def test_sends_c4_dmx_btn_for_keypad(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Keypad sends c4.dmx.btn with firmware value 03 (programmable)."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, behavior="keypad", led_mode="fixed")],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        btn_calls = [c for c in mock_mqtt.call_args_list if "c4.dmx.btn" in str(c)]
        assert len(btn_calls) == 1
        assert btn_calls[0][0][1] == {"c4_cmd": "c4.dmx.btn 01 01 03"}

    @pytest.mark.asyncio
    async def test_sends_c4_dmx_btn_for_load_on(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """load_on sends c4.dmx.btn with firmware value 00."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, behavior="load_on", led_mode="follow_load")],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        btn_calls = [c for c in mock_mqtt.call_args_list if "c4.dmx.btn" in str(c)]
        assert len(btn_calls) == 1
        assert btn_calls[0][0][1] == {"c4_cmd": "c4.dmx.btn 01 01 00"}

    @pytest.mark.asyncio
    async def test_sends_c4_dmx_btn_for_load_off(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """load_off sends c4.dmx.btn with firmware value 01."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=5, behavior="load_off", led_mode="follow_load")],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        btn_calls = [c for c in mock_mqtt.call_args_list if "c4.dmx.btn" in str(c)]
        assert len(btn_calls) == 1
        assert btn_calls[0][0][1] == {"c4_cmd": "c4.dmx.btn 04 01 01"}

    @pytest.mark.asyncio
    async def test_sends_mode_05_override(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """_push_slot_config sends mode 05 override for visible LED color."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="keypad",
                    led_mode="fixed",
                    led_on_color="00ff00",
                    led_off_color="ff0000",
                )
            ],
        )
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        override_calls = [
            c
            for c in mock_mqtt.call_args_list
            if "c4.dmx.led" in str(c) and " 05 " in str(c)
        ]
        assert len(override_calls) == 1
        # Fixed mode with no tracked entity → off_color
        assert override_calls[0][0][1] == {"c4_cmd": "c4.dmx.led 01 05 ff0000"}

    @pytest.mark.asyncio
    async def test_mode_05_uses_on_color_when_tracked_entity_is_on(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Mode 05 override uses on_color when tracked entity is on."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[
                SlotConfig(
                    slot_id=2,
                    behavior="toggle_load",
                    led_mode="follow_load",
                    led_on_color="ffffff",
                    led_off_color="000000",
                    led_track_entity_id="__self_load__",
                )
            ],
        )
        # Mock the light entity as ON
        light_state = MagicMock()
        light_state.entity_id = "light.kitchen"
        light_state.attributes = {"friendly_name": "Kitchen"}
        light_state.state = "on"
        manager._hass.states.async_all.return_value = [light_state]
        manager._hass.states.get.return_value = light_state
        with patch.object(
            manager, "async_send_mqtt", new_callable=AsyncMock
        ) as mock_mqtt:
            await manager._push_slot_config(dimmer_state, config)
        override_calls = [
            c
            for c in mock_mqtt.call_args_list
            if "c4.dmx.led" in str(c) and " 05 " in str(c)
        ]
        assert len(override_calls) == 1
        assert override_calls[0][0][1] == {"c4_cmd": "c4.dmx.led 01 05 ffffff"}


class TestGetDeviceInfo:
    """Verify get_device_info returns correct data for the card."""

    def test_includes_device_state_and_config(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, behavior="load_on", led_mode="follow_load")],
        )
        manager._store._devices[IEEE_DIMMER] = config
        info = manager.get_device_info(IEEE_DIMMER)
        assert info is not None
        assert info["state"] == "ON"
        assert info["device_type"] == "dimmer"
        assert info["config"]["slots"][0]["behavior"] == "load_on"
        assert info["config"]["slots"][0]["led_mode"] == "follow_load"

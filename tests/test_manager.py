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
        ids = [s.slot_id for s in slots]
        assert 2 in ids
        assert 5 in ids
        # Verify action fields are set
        top = next(s for s in slots if s.slot_id == 2)
        assert top.tap_action["action"] == "call-service"
        assert top.tap_action["service"] == "light.turn_on"

    def test_keypad_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypad")
        assert len(slots) == 6
        assert slots[0].slot_id == 1
        assert slots[5].slot_id == 6
        # All keypad buttons should fire events
        assert slots[0].tap_action == {"action": "fire-event"}

    def test_keypaddim_defaults(self, manager: Control4Manager) -> None:
        slots = manager.get_default_slots("keypaddim")
        assert len(slots) == 6
        # First slot should toggle load
        assert slots[0].tap_action["action"] == "toggle"
        assert slots[0].tap_action["target"]["entity_id"] == "__self_load__"
        # Other slots should fire events
        assert slots[1].tap_action == {"action": "fire-event"}


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
    """Tests for the _execute_slot_action method."""

    @pytest.mark.asyncio
    async def test_fire_event_action(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, tap_action={"action": "fire-event"})],
        )
        manager._store._devices[IEEE_DIMMER] = config
        # fire-event should not call any service
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_toggle_action(
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
                        "action": "toggle",
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
    async def test_call_service_action(
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
                        "action": "call-service",
                        "service": "light.turn_on",
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
                        "action": "toggle",
                        "target": {"entity_id": "__self_load__"},
                    },
                )
            ],
        )
        manager._store._devices[IEEE_DIMMER] = config
        # Mock Z2M light entity lookup
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
    async def test_none_action_does_nothing(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, tap_action={"action": "none"})],
        )
        manager._store._devices[IEEE_DIMMER] = config
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_not_called()

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
                    tap_action={"action": "fire-event"},
                    double_tap_action={
                        "action": "toggle",
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
    async def test_no_config_does_nothing(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        # No config stored
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        manager._hass.services.async_call.assert_not_called()


# ── Manager: dispatch with actions ──────────────────────────────────


class TestDispatchWithActions:
    """Tests for _dispatch_button_action with the new action system."""

    def test_press_executes_tap_immediately_when_no_double_tap(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        """Press executes tap_action immediately without double_tap."""
        manager._devices[IEEE_DIMMER] = dimmer_state
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, tap_action={"action": "fire-event"})],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager._dispatch_button_action(dimmer_state, "button_2_press")
        # Should have created a task for the action
        manager._hass.async_create_task.assert_called()

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
                    tap_action={"action": "fire-event"},
                    double_tap_action={
                        "action": "toggle",
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
                        "action": "toggle",
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
            slots=[SlotConfig(slot_id=2, tap_action={"action": "fire-event"})],
        )
        manager._store._devices[IEEE_DIMMER] = config
        manager.setup_light_tracking()
        manager._hass.bus.async_listen.assert_not_called()

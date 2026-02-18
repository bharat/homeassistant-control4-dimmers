"""Tests for entity platforms (button, event, light, select)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers.button import Control4ButtonEntity
from custom_components.control4_dimmers.const import (
    BUTTON_EVENT_TYPES,
    DOMAIN,
)
from custom_components.control4_dimmers.event import Control4ButtonEvent
from custom_components.control4_dimmers.light import (
    Control4DimmerLight,
    Control4LedLight,
)
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.select import Control4DeviceTypeSelect

from .conftest import IEEE_DIMMER

# ── Button entity ────────────────────────────────────────────────────


class TestControl4ButtonEntity:
    """Tests for the button press entity."""

    def _make_entity(self, manager: Control4Manager) -> Control4ButtonEntity:
        return Control4ButtonEntity(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            slot_id=1,
            slot_name="Top",
        )

    def test_unique_id(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.unique_id == f"{IEEE_DIMMER}_button_1"

    def test_name_from_slot(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.name == "Top"

    def test_name_fallback(self, manager: Control4Manager) -> None:
        entity = Control4ButtonEntity(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            slot_id=3,
            slot_name="",
        )
        assert entity.name == "Button 4"

    def test_device_info(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert (DOMAIN, IEEE_DIMMER) in entity.device_info["identifiers"]

    @pytest.mark.asyncio
    async def test_press_sends_mqtt(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        manager.async_send_mqtt = AsyncMock()
        await entity.async_press()
        manager.async_send_mqtt.assert_awaited_once_with(
            IEEE_DIMMER,
            {"c4_cmd": "c4.dmx.bp 01"},
        )


# ── Event entity ─────────────────────────────────────────────────────


class TestControl4ButtonEvent:
    """Tests for the event entity."""

    def _make_entity(
        self,
        manager: Control4Manager,
        slot_id: int = 1,
    ) -> Control4ButtonEvent:
        return Control4ButtonEvent(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-APD120",
            slot_id=slot_id,
        )

    def test_unique_id(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.unique_id == f"{IEEE_DIMMER}_event_1"

    def test_default_name_no_device(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.name == "Button 2"

    def test_default_name_from_device_type(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        entity = self._make_entity(manager, slot_id=1)
        assert entity.name == "Top"

    def test_default_name_bottom_slot(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        entity = self._make_entity(manager, slot_id=4)
        assert entity.name == "Bottom"

    def test_event_types(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.event_types == BUTTON_EVENT_TYPES

    def test_name_from_config(self, manager: Control4Manager) -> None:
        manager.store._devices[IEEE_DIMMER] = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=1, name="Interior")],
        )
        entity = self._make_entity(manager)
        assert entity.name == "Interior"

    def test_sync_name_from_config_returns_true_on_change(
        self, manager: Control4Manager
    ) -> None:
        entity = self._make_entity(manager)
        manager.store._devices[IEEE_DIMMER] = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=1, name="NewName")],
        )
        changed = entity._sync_name_from_config()
        assert changed is True
        assert entity.name == "NewName"

    def test_sync_name_returns_false_when_unchanged(
        self, manager: Control4Manager
    ) -> None:
        entity = self._make_entity(manager)
        changed = entity._sync_name_from_config()
        assert changed is False

    def test_on_button_event(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()
        entity._on_button_event("press")
        entity.async_write_ha_state.assert_called_once()

    def test_update_entity_id_on_rename(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        mock_registry_entry = MagicMock()
        mock_registry_entry.entity_id = "event.kitchen_button_2"
        entity.registry_entry = mock_registry_entry
        entity.entity_id = "event.kitchen_button_2"
        mock_ent_reg = MagicMock()
        with patch(
            "custom_components.control4_dimmers.event.er.async_get",
            return_value=mock_ent_reg,
        ):
            manager.store._devices[IEEE_DIMMER] = DeviceConfig(
                ieee_address=IEEE_DIMMER,
                friendly_name="Kitchen",
                device_type="dimmer",
                slots=[SlotConfig(slot_id=1, name="Main")],
            )
            entity._on_manager_update()
        mock_ent_reg.async_update_entity.assert_called_once_with(
            "event.kitchen_button_2", new_entity_id="event.kitchen_main"
        )


# ── LED Light entity ─────────────────────────────────────────────────


class TestControl4LedLight:
    """Tests for the LED light entity."""

    def _make_entity(
        self,
        manager: Control4Manager,
        mode: str = "on",
    ) -> Control4LedLight:
        return Control4LedLight(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            slot_id=1,
            slot_name="Top",
            mode=mode,
        )

    def test_unique_id(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager, mode="on")
        assert entity.unique_id == f"{IEEE_DIMMER}_led_1_on"

    def test_name_includes_mode(self, manager: Control4Manager) -> None:
        on_entity = self._make_entity(manager, mode="on")
        off_entity = self._make_entity(manager, mode="off")
        assert "On" in on_entity.name
        assert "Off" in off_entity.name

    def test_initial_state(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.is_on is True
        assert entity.brightness == 255

    @pytest.mark.asyncio
    async def test_turn_on_sends_mqtt(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()
        manager.async_send_mqtt = AsyncMock()
        await entity.async_turn_on(hs_color=(240.0, 100.0), brightness=200)
        manager.async_send_mqtt.assert_awaited_once()
        call_payload = manager.async_send_mqtt.call_args[0][1]
        assert "color_button_1_on" in call_payload

    @pytest.mark.asyncio
    async def test_turn_off_sends_mqtt(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()
        manager.async_send_mqtt = AsyncMock()
        await entity.async_turn_off()
        assert entity.is_on is False
        assert entity.brightness == 0
        manager.async_send_mqtt.assert_awaited_once()


# ── Dimmer Light entity ──────────────────────────────────────────────


class TestControl4DimmerLight:
    """Tests for the dimmer load light entity."""

    def _make_entity(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
    ) -> Control4DimmerLight:
        manager._devices[IEEE_DIMMER] = dimmer_state
        return Control4DimmerLight(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-APD120",
        )

    def test_unique_id(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.unique_id == f"{IEEE_DIMMER}_dimmer"

    def test_name(self, manager: Control4Manager, dimmer_state: DeviceState) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.name == "Dimmer"

    def test_initial_state_from_device(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.is_on is True
        assert entity.brightness == 200

    def test_initial_state_off(
        self,
        manager: Control4Manager,
    ) -> None:
        off_state = DeviceState(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-APD120",
            device_type="dimmer",
            brightness=0,
            state="OFF",
        )
        entity = self._make_entity(manager, off_state)
        assert entity.is_on is False
        assert entity.brightness == 0

    def test_extra_state_attributes(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.extra_state_attributes == {"ieee_address": IEEE_DIMMER}

    @pytest.mark.asyncio
    async def test_turn_on_sends_mqtt(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        manager.async_send_mqtt = AsyncMock()
        await entity.async_turn_on(brightness=180)
        manager.async_send_mqtt.assert_awaited_once_with(
            IEEE_DIMMER, {"state": "ON", "brightness": 180}
        )
        assert entity.is_on is True
        assert entity.brightness == 180

    @pytest.mark.asyncio
    async def test_turn_on_default_brightness(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        manager.async_send_mqtt = AsyncMock()
        await entity.async_turn_on()
        call_payload = manager.async_send_mqtt.call_args[0][1]
        assert call_payload["brightness"] == 200

    @pytest.mark.asyncio
    async def test_turn_off_sends_mqtt(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        manager.async_send_mqtt = AsyncMock()
        await entity.async_turn_off()
        manager.async_send_mqtt.assert_awaited_once_with(
            IEEE_DIMMER, {"state": "OFF", "brightness": 0}
        )
        assert entity.is_on is False
        assert entity.brightness == 0

    def test_listener_updates_state(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        assert entity.is_on is True
        dimmer_state.state = "OFF"
        dimmer_state.brightness = 0
        entity._on_manager_update()
        assert entity.is_on is False
        assert entity.brightness == 0
        entity.async_write_ha_state.assert_called_once()


# ── Select entity ────────────────────────────────────────────────────


class TestControl4DeviceTypeSelect:
    """Tests for the device-type select entity."""

    def _make_entity(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> Control4DeviceTypeSelect:
        manager._devices[IEEE_DIMMER] = dimmer_state
        return Control4DeviceTypeSelect(
            manager=manager,
            ieee_address=IEEE_DIMMER,
        )

    def test_unique_id(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.unique_id == f"{IEEE_DIMMER}_device_type"

    def test_current_option_from_state(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.current_option == "dimmer"

    def test_current_option_with_override(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
        dimmer_config: DeviceConfig,
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        dimmer_config.device_type_override = "keypad"
        manager.store._devices[IEEE_DIMMER] = dimmer_config
        assert entity.current_option == "keypad"

    def test_extra_state_attributes(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        attrs = entity.extra_state_attributes
        assert attrs["ieee_address"] == IEEE_DIMMER
        assert attrs["device_name"] == "Kitchen"
        assert attrs["model_id"] == "C4-APD120"
        assert attrs["detected_type"] == "dimmer"

    @pytest.mark.asyncio
    async def test_select_option(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        manager.async_configure_device = AsyncMock()
        await entity.async_select_option("keypaddim")
        manager.async_configure_device.assert_awaited_once_with(
            ieee_address=IEEE_DIMMER,
            device_type_override="keypaddim",
        )

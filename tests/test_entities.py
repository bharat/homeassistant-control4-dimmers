"""Tests for entity platforms (event, light, sensor)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.control4_dimmers.const import (
    BUTTON_EVENT_TYPES,
    DOMAIN,
)
from custom_components.control4_dimmers.event import (
    Control4ButtonEvent,
    Control4PaddleEvent,
)
from custom_components.control4_dimmers.event import (
    async_setup_entry as event_async_setup_entry,
)
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.sensor import Control4DeviceSensor

from .conftest import IEEE_DIMMER, IEEE_KEYPAD

# ── Event entity ─────────────────────────────────────────────────────


class TestControl4ButtonEvent:
    """Tests for the event entity."""

    def _make_entity(
        self,
        manager: Control4Manager,
        slot_id: int = 2,
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
        assert entity.unique_id == f"{IEEE_DIMMER}_event_2"

    def test_default_name_no_device(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.name == "Button 2"

    def test_default_name_from_device_type(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        entity = self._make_entity(manager, slot_id=2)
        assert entity.name == "Top"

    def test_default_name_bottom_slot(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        entity = self._make_entity(manager, slot_id=5)
        assert entity.name == "Bottom"

    def test_event_types(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.event_types == BUTTON_EVENT_TYPES

    def test_name_from_config(self, manager: Control4Manager) -> None:
        manager.store._devices[IEEE_DIMMER] = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=2, name="Interior")],
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
            slots=[SlotConfig(slot_id=2, name="NewName")],
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
        entity._on_button_event("pressed")
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
                slots=[SlotConfig(slot_id=2, name="Main")],
            )
            entity._on_manager_update()
        mock_ent_reg.async_update_entity.assert_called_once_with(
            "event.kitchen_button_2", new_entity_id="event.kitchen_main"
        )

    def test_extra_state_attributes_from_config(self, manager: Control4Manager) -> None:
        manager.store._devices[IEEE_DIMMER] = DeviceConfig(
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
                )
            ],
        )
        entity = self._make_entity(manager, slot_id=2)
        attrs = entity.extra_state_attributes
        assert attrs["on_color"] == "#ffffff"
        assert attrs["off_color"] == "#000000"
        assert attrs["behavior"] == "load_on"
        assert attrs["led_mode"] == "follow_load"
        assert attrs["ieee_address"] == IEEE_DIMMER
        assert attrs["slot_id"] == 2

    def test_extra_state_attributes_defaults(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager, slot_id=3)
        attrs = entity.extra_state_attributes
        assert attrs["on_color"] == "#0000ff"
        assert attrs["off_color"] == "#000000"
        assert attrs["behavior"] == "keypad"
        assert attrs["led_mode"] == "fixed"


# ── Paddle event entity (issue #117) ─────────────────────────────────


class TestControl4PaddleEvent:
    """Tests for the local load paddle event entity."""

    def _make_entity(
        self,
        manager: Control4Manager,
        paddle_id: str = "paddle_up",
    ) -> Control4PaddleEvent:
        return Control4PaddleEvent(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-APD120",
            paddle_id=paddle_id,
        )

    def test_unique_id_up(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager, "paddle_up")
        assert entity.unique_id == f"{IEEE_DIMMER}_event_paddle_up"

    def test_unique_id_down(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager, "paddle_down")
        assert entity.unique_id == f"{IEEE_DIMMER}_event_paddle_down"

    def test_names(self, manager: Control4Manager) -> None:
        assert self._make_entity(manager, "paddle_up").name == "Paddle Up"
        assert self._make_entity(manager, "paddle_down").name == "Paddle Down"

    def test_event_types(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.event_types == BUTTON_EVENT_TYPES

    def test_extra_state_attributes(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager, "paddle_down")
        attrs = entity.extra_state_attributes
        assert attrs["ieee_address"] == IEEE_DIMMER
        assert attrs["paddle"] == "paddle_down"

    def test_on_paddle_event(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()
        entity._on_paddle_event("pressed")
        entity.async_write_ha_state.assert_called_once()


class TestEventSetupCreatesPaddles:
    """Tests for paddle entity creation via async_setup_entry."""

    async def _run_setup(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
    ) -> list:
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
        added: list = []
        await event_async_setup_entry(mock_hass, mock_entry, added.extend)
        return added

    async def test_paddles_created_for_dimmer(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
        dimmer_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        added = await self._run_setup(manager, mock_hass, mock_entry)
        uids = {e.unique_id for e in added}
        assert f"{IEEE_DIMMER}_event_paddle_up" in uids
        assert f"{IEEE_DIMMER}_event_paddle_down" in uids

    async def test_paddles_created_for_keypaddim(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
    ) -> None:
        manager._devices[IEEE_DIMMER] = DeviceState(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-KD120",
            device_type="keypaddim",
        )
        added = await self._run_setup(manager, mock_hass, mock_entry)
        uids = {e.unique_id for e in added}
        assert f"{IEEE_DIMMER}_event_paddle_up" in uids
        assert f"{IEEE_DIMMER}_event_paddle_down" in uids

    async def test_no_paddles_for_keypad(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
        keypad_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_KEYPAD] = keypad_state
        added = await self._run_setup(manager, mock_hass, mock_entry)
        uids = {e.unique_id for e in added}
        assert not any("paddle" in uid for uid in uids)
        # Six button slots, no paddle entities.
        assert len([e for e in added if isinstance(e, Control4PaddleEvent)]) == 0

    async def test_paddles_appear_after_reclassification(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
    ) -> None:
        # A device that starts unknown gets no paddle entities, then appears
        # once the self-heal reclassifies it to keypaddim (issue #117).
        state = DeviceState(
            ieee_address=IEEE_DIMMER,
            friendly_name="Entry",
            model_id="C4-APD120",
            device_type="",
        )
        manager._devices[IEEE_DIMMER] = state
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
        added: list = []
        await event_async_setup_entry(mock_hass, mock_entry, added.extend)
        assert added == []

        # Reclassification arrives via the manager listener path.
        state.device_type = "keypaddim"
        manager.notify_listeners()
        uids = {e.unique_id for e in added}
        assert f"{IEEE_DIMMER}_event_paddle_up" in uids
        assert f"{IEEE_DIMMER}_event_paddle_down" in uids


# ── Sensor anchor entity ─────────────────────────────────────────────


class TestControl4DeviceSensor:
    """Tests for the sensor anchor entity."""

    def _make_entity(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> Control4DeviceSensor:
        manager._devices[IEEE_DIMMER] = dimmer_state
        return Control4DeviceSensor(
            manager=manager,
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            model_id="C4-APD120",
        )

    def test_unique_id(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.unique_id == f"{IEEE_DIMMER}_sensor"

    def test_name_is_none(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.name is None

    def test_native_value_connected(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        assert entity.native_value == "connected"

    def test_native_value_disconnected(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        dimmer_state.available = False
        entity = self._make_entity(manager, dimmer_state)
        assert entity.native_value == "disconnected"

    def test_extra_state_attributes(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        attrs = entity.extra_state_attributes
        assert attrs["ieee_address"] == IEEE_DIMMER
        assert attrs["detected_type"] == "dimmer"
        assert attrs["model_id"] == "C4-APD120"

    def test_extra_state_attributes_with_config(
        self,
        manager: Control4Manager,
        dimmer_state: DeviceState,
        dimmer_config: DeviceConfig,
    ) -> None:
        manager.store._devices[IEEE_DIMMER] = dimmer_config
        entity = self._make_entity(manager, dimmer_state)
        attrs = entity.extra_state_attributes
        assert attrs["device_type"] == "dimmer"

    def test_listener_writes_state(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        entity = self._make_entity(manager, dimmer_state)
        entity.async_write_ha_state = MagicMock()
        entity._on_manager_update()
        entity.async_write_ha_state.assert_called_once()

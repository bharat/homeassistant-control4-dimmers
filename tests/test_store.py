"""Tests for Control4Store persistent storage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers.models import DeviceConfig, SlotConfig
from custom_components.control4_dimmers.store import Control4Store, _migrate_slot

from .conftest import IEEE_DIMMER


class TestControl4Store:
    """Tests for the persistent store."""

    @pytest.fixture
    def store(self) -> Control4Store:
        """Return a store with a mocked HA Store backend."""
        with patch("custom_components.control4_dimmers.store.Store") as mock_cls:
            mock_ha_store = mock_cls.return_value
            mock_ha_store.async_load = AsyncMock(return_value=None)
            mock_ha_store.async_save = AsyncMock()
            s = Control4Store(MagicMock(), "entry1")
        s._store = mock_ha_store
        return s

    @pytest.mark.asyncio
    async def test_load_empty(self, store: Control4Store) -> None:
        store._store.async_load.return_value = None
        await store.async_load()
        assert store.devices == {}

    @pytest.mark.asyncio
    async def test_load_with_data(self, store: Control4Store) -> None:
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [{"slot_id": 1, "name": "Top"}],
                }
            }
        }
        await store.async_load()
        assert IEEE_DIMMER in store.devices
        assert store.devices[IEEE_DIMMER].friendly_name == "Kitchen"
        assert len(store.devices[IEEE_DIMMER].slots) == 1

    @pytest.mark.asyncio
    async def test_load_invalid_data(self, store: Control4Store) -> None:
        store._store.async_load.return_value = "not a dict"
        await store.async_load()
        assert store.devices == {}

    @pytest.mark.asyncio
    async def test_save_device(self, store: Control4Store) -> None:
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=1, name="Top")],
        )
        await store.async_save_device(config)
        assert store.get_device(IEEE_DIMMER) is config
        store._store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_save_serializes_all(self, store: Control4Store) -> None:
        store._devices[IEEE_DIMMER] = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
        )
        await store.async_save()
        call_args = store._store.async_save.call_args[0][0]
        assert "devices" in call_args
        assert IEEE_DIMMER in call_args["devices"]

    def test_get_device_returns_none(self, store: Control4Store) -> None:
        assert store.get_device("0xNOTHERE") is None

    def test_get_device_returns_config(self, store: Control4Store) -> None:
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
        )
        store._devices[IEEE_DIMMER] = config
        assert store.get_device(IEEE_DIMMER) is config

    @pytest.mark.asyncio
    async def test_migration_from_behavior(self, store: Control4Store) -> None:
        """Load behaviors should be preserved as firmware behaviors."""
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [
                        {"slot_id": 2, "name": "Top", "behavior": "load_on"},
                        {"slot_id": 5, "name": "Bottom", "behavior": "load_off"},
                    ],
                }
            }
        }
        await store.async_load()
        config = store.get_device(IEEE_DIMMER)
        assert config is not None
        # Load behaviors stay as firmware behaviors, no tap_action
        top = config.slots[0]
        assert top.behavior == "load_on"
        assert top.tap_action is None
        assert top.led_mode == "follow_load"
        bottom = config.slots[1]
        assert bottom.behavior == "load_off"
        assert bottom.tap_action is None

    @pytest.mark.asyncio
    async def test_migration_from_intermediate_format(
        self, store: Control4Store
    ) -> None:
        """Migrate intermediate formats; __self_load__ becomes firmware behavior."""
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [
                        {
                            "slot_id": 2,
                            "tap_action": {"action": "fire-event"},
                        },
                        {
                            "slot_id": 3,
                            "tap_action": {
                                "action": "call-service",
                                "service": "light.turn_on",
                                "target": {"entity_id": "__self_load__"},
                            },
                        },
                        {
                            "slot_id": 4,
                            "tap_action": {
                                "action": "toggle",
                                "target": {"entity_id": "light.kitchen"},
                            },
                        },
                    ],
                }
            }
        }
        await store.async_load()
        config = store.get_device(IEEE_DIMMER)
        assert config is not None
        # fire-event → None (programmable, no action)
        assert config.slots[0].tap_action is None
        # __self_load__ call-service → firmware behavior
        assert config.slots[1].behavior == "load_on"
        assert config.slots[1].tap_action is None
        # non-self toggle → kept as HA action
        assert config.slots[2].tap_action == {
            "action": "light.toggle",
            "target": {"entity_id": "light.kitchen"},
        }
        store._store.async_save.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_migration_when_already_native(self, store: Control4Store) -> None:
        """Slots already in HA-native format should not be re-migrated."""
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [
                        {
                            "slot_id": 2,
                            "led_mode": "fixed",
                            "tap_action": {
                                "action": "light.toggle",
                                "target": {"entity_id": "light.kitchen"},
                            },
                        },
                    ],
                }
            }
        }
        await store.async_load()
        config = store.get_device(IEEE_DIMMER)
        assert config is not None
        assert config.slots[0].tap_action == {
            "action": "light.toggle",
            "target": {"entity_id": "light.kitchen"},
        }
        store._store.async_save.assert_not_awaited()


class TestMigrateSlot:
    """Tests for the _migrate_slot helper."""

    def test_migrate_keypad_behavior(self) -> None:
        """Keypad behavior has no action to migrate, but led_mode migrates."""
        slot = SlotConfig(slot_id=1, behavior="keypad")
        assert _migrate_slot(slot) is True
        assert slot.tap_action is None
        assert slot.behavior == "keypad"
        assert slot.led_mode == "fixed"  # Phase 3: programmed→fixed

    def test_migrate_control_light_behavior(self) -> None:
        slot = SlotConfig(
            slot_id=1, behavior="control_light", target_entity_id="light.kitchen"
        )
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {
            "action": "light.toggle",
            "target": {"entity_id": "light.kitchen"},
        }
        assert slot.led_track_entity_id == "light.kitchen"
        assert slot.target_entity_id is None

    def test_load_behaviors_stay_as_firmware(self) -> None:
        """Load behaviors are NOT converted to tap_actions."""
        for behavior in ("toggle_load", "load_on", "load_off"):
            slot = SlotConfig(slot_id=1, behavior=behavior)
            _migrate_slot(slot)
            assert slot.behavior == behavior
            assert slot.tap_action is None

    def test_migrate_fire_event_to_null(self) -> None:
        slot = SlotConfig(slot_id=1, tap_action={"action": "fire-event"})
        assert _migrate_slot(slot) is True
        assert slot.tap_action is None

    def test_migrate_none_to_null(self) -> None:
        slot = SlotConfig(slot_id=1, tap_action={"action": "none"})
        assert _migrate_slot(slot) is True
        assert slot.tap_action is None

    def test_migrate_intermediate_toggle(self) -> None:
        slot = SlotConfig(
            slot_id=1,
            tap_action={"action": "toggle", "target": {"entity_id": "light.x"}},
        )
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {
            "action": "light.toggle",
            "target": {"entity_id": "light.x"},
        }

    def test_migrate_intermediate_call_service_self_load(self) -> None:
        """__self_load__ intermediate actions become firmware behaviors."""
        slot = SlotConfig(
            slot_id=1,
            tap_action={
                "action": "call-service",
                "service": "light.turn_on",
                "target": {"entity_id": "__self_load__"},
            },
        )
        assert _migrate_slot(slot) is True
        assert slot.behavior == "load_on"
        assert slot.tap_action is None

    def test_migrate_intermediate_call_service_non_self(self) -> None:
        """Non-self call-service actions stay as HA-native tap_actions."""
        slot = SlotConfig(
            slot_id=1,
            led_mode="fixed",
            tap_action={
                "action": "call-service",
                "service": "light.turn_on",
                "target": {"entity_id": "light.kitchen"},
            },
        )
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {
            "action": "light.turn_on",
            "target": {"entity_id": "light.kitchen"},
        }

    def test_skip_already_native(self) -> None:
        slot = SlotConfig(
            slot_id=1,
            led_mode="fixed",
            tap_action={"action": "light.toggle", "target": {"entity_id": "light.x"}},
        )
        assert _migrate_slot(slot) is False

    def test_skip_empty_behavior(self) -> None:
        slot = SlotConfig(slot_id=1, behavior="", led_mode="fixed")
        assert _migrate_slot(slot) is False

    def test_migrate_programmed_without_tracking_to_fixed(self) -> None:
        slot = SlotConfig(slot_id=1, led_mode="programmed")
        assert _migrate_slot(slot) is True
        assert slot.led_mode == "fixed"

    def test_migrate_ha_native_self_load_to_behavior(self) -> None:
        """HA-native __self_load__ tap_actions get converted to behavior."""
        slot = SlotConfig(
            slot_id=1,
            led_mode="fixed",
            tap_action={
                "action": "light.toggle",
                "target": {"entity_id": "__self_load__"},
            },
        )
        assert _migrate_slot(slot) is True
        assert slot.behavior == "toggle_load"
        assert slot.tap_action is None
        assert slot.led_mode == "follow_load"

    def test_keep_programmed_with_tracking(self) -> None:
        slot = SlotConfig(
            slot_id=1,
            led_mode="programmed",
            led_track_entity_id="light.kitchen",
        )
        assert _migrate_slot(slot) is False

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
    async def test_migration_on_load(self, store: Control4Store) -> None:
        """Loading old-format configs should auto-migrate behavior to tap_action."""
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
        top = config.slots[0]
        assert top.tap_action is not None
        assert top.tap_action["action"] == "call-service"
        assert top.tap_action["service"] == "light.turn_on"
        bottom = config.slots[1]
        assert bottom.tap_action["action"] == "call-service"
        assert bottom.tap_action["service"] == "light.turn_off"
        # behavior should be reset
        assert top.behavior == "keypad"
        # Store should have been saved
        store._store.async_save.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_migration_when_already_migrated(
        self, store: Control4Store
    ) -> None:
        """Slots that already have tap_action should not be re-migrated."""
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [
                        {
                            "slot_id": 2,
                            "name": "Top",
                            "tap_action": {"action": "fire-event"},
                        },
                    ],
                }
            }
        }
        await store.async_load()
        config = store.get_device(IEEE_DIMMER)
        assert config is not None
        assert config.slots[0].tap_action == {"action": "fire-event"}
        store._store.async_save.assert_not_awaited()


class TestMigrateSlot:
    """Tests for the _migrate_slot helper."""

    def test_migrate_keypad(self) -> None:
        slot = SlotConfig(slot_id=1, behavior="keypad")
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {"action": "fire-event"}
        assert slot.behavior == "keypad"
        assert slot.led_track_entity_id is None

    def test_migrate_control_light(self) -> None:
        slot = SlotConfig(
            slot_id=1, behavior="control_light", target_entity_id="light.kitchen"
        )
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {
            "action": "toggle",
            "target": {"entity_id": "light.kitchen"},
        }
        assert slot.led_track_entity_id == "light.kitchen"
        assert slot.target_entity_id is None

    def test_migrate_toggle_load(self) -> None:
        slot = SlotConfig(slot_id=1, behavior="toggle_load")
        assert _migrate_slot(slot) is True
        assert slot.tap_action == {
            "action": "toggle",
            "target": {"entity_id": "__self_load__"},
        }

    def test_migrate_load_on(self) -> None:
        slot = SlotConfig(slot_id=2, behavior="load_on")
        assert _migrate_slot(slot) is True
        assert slot.tap_action["action"] == "call-service"
        assert slot.tap_action["service"] == "light.turn_on"

    def test_migrate_load_off(self) -> None:
        slot = SlotConfig(slot_id=5, behavior="load_off")
        assert _migrate_slot(slot) is True
        assert slot.tap_action["action"] == "call-service"
        assert slot.tap_action["service"] == "light.turn_off"

    def test_skip_already_migrated(self) -> None:
        slot = SlotConfig(slot_id=1, tap_action={"action": "fire-event"})
        assert _migrate_slot(slot) is False

    def test_skip_empty_behavior(self) -> None:
        slot = SlotConfig(slot_id=1, behavior="")
        assert _migrate_slot(slot) is False

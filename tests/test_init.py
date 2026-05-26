"""Tests for integration setup and teardown (__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers import (
    _get_runtime,
    _svc_set_slot_led,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.control4_dimmers.const import DOMAIN
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)


class TestGetRuntime:
    """Tests for the _get_runtime helper."""

    def test_returns_none_when_empty(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {}
        assert _get_runtime(mock_hass) is None

    def test_returns_none_when_no_manager(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {DOMAIN: {"entry1": {"other": True}}}
        assert _get_runtime(mock_hass) is None

    def test_returns_runtime(self, mock_hass: MagicMock) -> None:
        runtime = {"manager": MagicMock(), "store": MagicMock()}
        mock_hass.data = {DOMAIN: {"entry1": runtime}}
        assert _get_runtime(mock_hass) is runtime


class TestAsyncSetup:
    """Tests for async_setup (one-time integration setup)."""

    @pytest.mark.asyncio
    async def test_sets_domain_data(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {}
        with (
            patch(
                "custom_components.control4_dimmers._register_websocket_handlers",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.control4_dimmers._register_services",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.control4_dimmers._register_frontend",
                new_callable=AsyncMock,
            ),
        ):
            result = await async_setup(mock_hass, {})
        assert result is True
        assert DOMAIN in mock_hass.data


class TestSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_creates_manager_and_store(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_hass.data = {DOMAIN: {}, f"{DOMAIN}_skip_mqtt": True}
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()

        with (
            patch("custom_components.control4_dimmers.Control4Store") as mock_store_cls,
            patch("custom_components.control4_dimmers.Control4Manager") as mock_mgr_cls,
        ):
            mock_store_cls.return_value.async_load = AsyncMock()
            mock_mgr_cls.return_value.async_start = AsyncMock()
            result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True
        entry_data = mock_hass.data[DOMAIN][mock_entry.entry_id]
        assert "manager" in entry_data
        assert "store" in entry_data


class TestUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_cleans_up(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_manager = MagicMock()
        mock_manager.async_stop = AsyncMock()
        mock_hass.data = {
            DOMAIN: {
                mock_entry.entry_id: {
                    "manager": mock_manager,
                    "store": MagicMock(),
                }
            }
        }
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(mock_hass, mock_entry)
        assert result is True
        mock_manager.async_stop.assert_awaited_once()
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_returns_false_on_failure(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": MagicMock()}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(mock_hass, mock_entry)
        assert result is False


class TestFindLightEntity:
    """Tests for the _find_light_entity helper (MQTT+ approach), now on manager."""

    def _make_manager(
        self, mock_hass: MagicMock, friendly_name: str = "Kitchen"
    ) -> Control4Manager:
        """Create a manager with a device."""
        entry = MagicMock()
        entry.data = {"mqtt_topic": "zigbee2mqtt"}
        entry.options = {}
        store = MagicMock()
        mgr = Control4Manager(mock_hass, entry, store)
        mgr._devices["0xAABB"] = DeviceState(
            ieee_address="0xAABB", friendly_name=friendly_name
        )
        return mgr

    def test_finds_z2m_light_by_friendly_name(self, mock_hass: MagicMock) -> None:
        mgr = self._make_manager(mock_hass, "Kitchen")
        light_state = MagicMock()
        light_state.entity_id = "light.kitchen"
        light_state.attributes = {"friendly_name": "Kitchen"}
        mock_hass.states.async_all.return_value = [light_state]

        result = mgr._find_light_entity("0xAABB")
        assert result == "light.kitchen"

    def test_returns_none_when_no_match(self, mock_hass: MagicMock) -> None:
        mgr = self._make_manager(mock_hass, "Kitchen")
        light_state = MagicMock()
        light_state.entity_id = "light.other"
        light_state.attributes = {"friendly_name": "Other Room"}
        mock_hass.states.async_all.return_value = [light_state]

        result = mgr._find_light_entity("0xAABB")
        assert result is None

    def test_returns_none_when_no_lights(self, mock_hass: MagicMock) -> None:
        mgr = self._make_manager(mock_hass)
        mock_hass.states.async_all.return_value = []
        result = mgr._find_light_entity("0xAABB")
        assert result is None

    def test_returns_none_for_unknown_device(self, mock_hass: MagicMock) -> None:
        mgr = self._make_manager(mock_hass)
        result = mgr._find_light_entity("0xNONE")
        assert result is None


class TestSetSlotLedService:
    """Tests for the set_slot_led service handler."""

    def _setup(
        self,
        mock_hass: MagicMock,
        initial_slot: SlotConfig,
    ) -> tuple[MagicMock, Control4Manager, DeviceConfig]:
        ieee = "0xAABB"
        entry = MagicMock()
        entry.data = {"mqtt_topic": "zigbee2mqtt"}
        entry.options = {}
        store = MagicMock()
        config = DeviceConfig(
            ieee_address=ieee,
            friendly_name="Test Keypad",
            device_type="keypad",
            slots=[initial_slot],
        )
        store.get_device.return_value = config
        store.async_save_device = AsyncMock()
        mgr = Control4Manager(mock_hass, entry, store)
        mgr._devices[ieee] = DeviceState(ieee_address=ieee, friendly_name="Test Keypad")
        mock_hass.data = {DOMAIN: {"entry1": {"manager": mgr, "store": store}}}
        # Mock the event entity's state with the attributes the service
        # expects (ieee_address and slot_id).
        entity_state = MagicMock()
        entity_state.attributes = {
            "ieee_address": ieee,
            "slot_id": initial_slot.slot_id,
        }
        mock_hass.states.get.return_value = entity_state
        return entry, mgr, config

    @pytest.mark.asyncio
    async def test_updates_mode_only(self, mock_hass: MagicMock) -> None:
        initial = SlotConfig(
            slot_id=2,
            behavior="keypad",
            led_mode="fixed",
            led_on_color="ff0000",
            led_off_color="0000ff",
        )
        _, mgr, config = self._setup(mock_hass, initial)
        with patch.object(mgr, "_push_slot_config", new_callable=AsyncMock) as push:
            call = MagicMock()
            call.data = {
                "entity_id": "event.test_button_2",
                "led_mode": "push_release",
            }
            await _svc_set_slot_led(mock_hass, call)
        assert config.slots[0].led_mode == "push_release"
        # Colors are untouched when not provided.
        assert config.slots[0].led_on_color == "ff0000"
        assert config.slots[0].led_off_color == "0000ff"
        push.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_colors_only(self, mock_hass: MagicMock) -> None:
        initial = SlotConfig(
            slot_id=2,
            behavior="keypad",
            led_mode="push_release",
            led_on_color="ff0000",
            led_off_color="0000ff",
        )
        _, mgr, config = self._setup(mock_hass, initial)
        with patch.object(mgr, "_push_slot_config", new_callable=AsyncMock) as push:
            call = MagicMock()
            call.data = {
                "entity_id": "event.test_button_2",
                "on_color": "#00ff00",
                "off_color": "ffffff",
            }
            await _svc_set_slot_led(mock_hass, call)
        # The "#" prefix is stripped.
        assert config.slots[0].led_on_color == "00ff00"
        assert config.slots[0].led_off_color == "ffffff"
        # Mode is untouched.
        assert config.slots[0].led_mode == "push_release"
        push.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_all_in_one_call(self, mock_hass: MagicMock) -> None:
        initial = SlotConfig(
            slot_id=2,
            behavior="keypad",
            led_mode="fixed",
            led_on_color="000000",
            led_off_color="000000",
        )
        _, mgr, config = self._setup(mock_hass, initial)
        with patch.object(mgr, "_push_slot_config", new_callable=AsyncMock):
            call = MagicMock()
            call.data = {
                "entity_id": "event.test_button_2",
                "led_mode": "push_release",
                "on_color": "00ff00",
                "off_color": "ff0000",
            }
            await _svc_set_slot_led(mock_hass, call)
        assert config.slots[0].led_mode == "push_release"
        assert config.slots[0].led_on_color == "00ff00"
        assert config.slots[0].led_off_color == "ff0000"

    @pytest.mark.asyncio
    async def test_missing_entity_is_noop(self, mock_hass: MagicMock) -> None:
        mock_hass.states.get.return_value = None
        call = MagicMock()
        call.data = {"entity_id": "event.missing", "led_mode": "fixed"}
        # Should not raise; just logs and returns.
        await _svc_set_slot_led(mock_hass, call)

    @pytest.mark.asyncio
    async def test_unknown_slot_is_noop(self, mock_hass: MagicMock) -> None:
        initial = SlotConfig(slot_id=2, behavior="keypad", led_mode="fixed")
        _, mgr, _ = self._setup(mock_hass, initial)
        # Override the entity's slot_id to one that isn't in the config.
        entity_state = MagicMock()
        entity_state.attributes = {"ieee_address": "0xAABB", "slot_id": 99}
        mock_hass.states.get.return_value = entity_state
        with patch.object(mgr, "_push_slot_config", new_callable=AsyncMock) as push:
            call = MagicMock()
            call.data = {"entity_id": "event.test", "led_mode": "fixed"}
            await _svc_set_slot_led(mock_hass, call)
        push.assert_not_awaited()

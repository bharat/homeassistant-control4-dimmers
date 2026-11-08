"""Tests for the switch platform (relay-only loads, issue #189)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import Platform

from custom_components.control4_dimmers import PLATFORMS
from custom_components.control4_dimmers.const import (
    DEVICE_TYPE_SWITCH,
    DIMMABLE_TYPES,
    DOMAIN,
    LOAD_BEARING_TYPES,
)
from custom_components.control4_dimmers.light import (
    async_setup_entry as light_async_setup_entry,
)
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import DeviceConfig, DeviceState
from custom_components.control4_dimmers.switch import (
    Control4LoadSwitch,
)
from custom_components.control4_dimmers.switch import (
    async_setup_entry as switch_async_setup_entry,
)

from .conftest import IEEE_DIMMER, IEEE_KEYPAD

IEEE_SWITCH = "0x000fff0000ddd001"


def _switch_state(device_type: str | None = DEVICE_TYPE_SWITCH) -> DeviceState:
    return DeviceState(
        ieee_address=IEEE_SWITCH,
        friendly_name="Bath Fan",
        model_id="C4-Zigbee",
        device_type=device_type,
        state="OFF",
        brightness=0,
    )


async def _run_switch_setup(
    manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
) -> list:
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
    added: list = []
    await switch_async_setup_entry(mock_hass, mock_entry, added.extend)
    return added


async def _run_light_setup(
    manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
) -> list:
    mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": manager}}}
    added: list = []
    await light_async_setup_entry(mock_hass, mock_entry, added.extend)
    return added


# ── Platform registration ────────────────────────────────────────────


class TestPlatformRegistration:
    def test_switch_platform_registered(self) -> None:
        assert Platform.SWITCH in PLATFORMS

    def test_light_platform_still_not_registered(self) -> None:
        # Dimmable loads keep using Z2M's native light entity.
        assert Platform.LIGHT not in PLATFORMS

    def test_switch_is_load_bearing_but_not_dimmable(self) -> None:
        assert DEVICE_TYPE_SWITCH in LOAD_BEARING_TYPES
        assert DEVICE_TYPE_SWITCH not in DIMMABLE_TYPES


# ── Entity ───────────────────────────────────────────────────────────


class TestControl4LoadSwitch:
    def _make_entity(self, manager: Control4Manager) -> Control4LoadSwitch:
        return Control4LoadSwitch(
            manager=manager,
            ieee_address=IEEE_SWITCH,
            friendly_name="Bath Fan",
            model_id="C4-Zigbee",
        )

    def test_unique_id_and_name(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.unique_id == f"{IEEE_SWITCH}_switch"
        assert entity.name == "Load"

    def test_does_not_poll(self, manager: Control4Manager) -> None:
        assert self._make_entity(manager).should_poll is False

    def test_device_info_anchors_to_device(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.device_info["identifiers"] == {(DOMAIN, IEEE_SWITCH)}

    def test_extra_state_attributes_expose_ieee(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.extra_state_attributes == {"ieee_address": IEEE_SWITCH}

    def test_is_on_follows_device_state(self, manager: Control4Manager) -> None:
        state = _switch_state()
        manager._devices[IEEE_SWITCH] = state
        entity = self._make_entity(manager)
        assert entity.is_on is False
        state.update_from_mqtt({"state": "ON", "brightness": 254})
        entity._sync_state()
        assert entity.is_on is True

    def test_no_brightness_anywhere(self, manager: Control4Manager) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        entity = self._make_entity(manager)
        assert not hasattr(entity, "brightness")
        assert "brightness" not in (entity.extra_state_attributes or {})

    def test_unavailable_without_device(self, manager: Control4Manager) -> None:
        entity = self._make_entity(manager)
        assert entity.available is False

    def test_available_tracks_device_and_type(self, manager: Control4Manager) -> None:
        state = _switch_state()
        manager._devices[IEEE_SWITCH] = state
        entity = self._make_entity(manager)
        assert entity.available is True

        # Re-typed away from switch: the entity stops claiming the load.
        manager.store._devices[IEEE_SWITCH] = DeviceConfig(
            ieee_address=IEEE_SWITCH,
            friendly_name="Bath Fan",
            device_type=DEVICE_TYPE_SWITCH,
            device_type_override="keypaddim",
        )
        entity._sync_state()
        assert entity.available is False

    async def test_turn_on_sends_plain_state(self, manager: Control4Manager) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        manager.async_send_mqtt = AsyncMock()
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on()

        manager.async_send_mqtt.assert_awaited_once_with(IEEE_SWITCH, {"state": "ON"})
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called_once()

    async def test_turn_off_sends_plain_state(self, manager: Control4Manager) -> None:
        state = _switch_state()
        state.state = "ON"
        manager._devices[IEEE_SWITCH] = state
        manager.async_send_mqtt = AsyncMock()
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_off()

        manager.async_send_mqtt.assert_awaited_once_with(IEEE_SWITCH, {"state": "OFF"})
        assert entity.is_on is False

    async def test_turn_on_ignores_brightness_kwargs(
        self, manager: Control4Manager
    ) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        manager.async_send_mqtt = AsyncMock()
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on(brightness=128)

        payload = manager.async_send_mqtt.await_args.args[1]
        assert payload == {"state": "ON"}
        assert "brightness" not in payload

    async def test_listener_lifecycle(self, manager: Control4Manager) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        entity = self._make_entity(manager)
        entity.async_write_ha_state = MagicMock()

        await entity.async_added_to_hass()
        manager.notify_listeners()
        entity.async_write_ha_state.assert_called_once()

        await entity.async_will_remove_from_hass()
        manager.notify_listeners()
        entity.async_write_ha_state.assert_called_once()


# ── Platform setup ───────────────────────────────────────────────────


class TestSwitchSetup:
    async def test_creates_switch_for_switch_type(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        added = await _run_switch_setup(manager, mock_hass, mock_entry)
        assert [e.unique_id for e in added] == [f"{IEEE_SWITCH}_switch"]
        assert isinstance(added[0], Control4LoadSwitch)

    async def test_no_switch_for_dimmer_or_keypad(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
        dimmer_state: DeviceState,
        keypad_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._devices[IEEE_KEYPAD] = keypad_state
        manager._devices["0x000fff0000bbb001"] = DeviceState(
            ieee_address="0x000fff0000bbb001",
            friendly_name="Dining",
            device_type="keypaddim",
        )
        added = await _run_switch_setup(manager, mock_hass, mock_entry)
        assert added == []

    async def test_override_to_switch_creates_entity(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        # Detection said keypaddim; the user flagged it by hand (issue #189).
        manager._devices[IEEE_SWITCH] = _switch_state(device_type="keypaddim")
        manager.store._devices[IEEE_SWITCH] = DeviceConfig(
            ieee_address=IEEE_SWITCH,
            friendly_name="Bath Fan",
            device_type="keypaddim",
            device_type_override=DEVICE_TYPE_SWITCH,
        )
        added = await _run_switch_setup(manager, mock_hass, mock_entry)
        assert [e.unique_id for e in added] == [f"{IEEE_SWITCH}_switch"]

    async def test_switch_appears_after_reclassification(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        state = _switch_state(device_type=None)
        manager._devices[IEEE_SWITCH] = state
        added = await _run_switch_setup(manager, mock_hass, mock_entry)
        assert added == []

        # The converter's keypaddim verdict with dim_code 00 arrives later.
        state.update_from_mqtt(
            {
                "c4_device_type": "keypaddim",
                "c4_detect_result": {"device_type": "keypaddim", "dim_code": "00"},
            }
        )
        manager.notify_listeners()
        assert [e.unique_id for e in added] == [f"{IEEE_SWITCH}_switch"]

    async def test_switch_added_once(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        added = await _run_switch_setup(manager, mock_hass, mock_entry)
        manager.notify_listeners()
        manager.notify_listeners()
        assert len(added) == 1

    async def test_returns_when_runtime_missing(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_hass.data = {DOMAIN: {}}
        added: list = []
        await switch_async_setup_entry(mock_hass, mock_entry, added.extend)
        assert added == []


# ── No light entity for a switch ─────────────────────────────────────


class TestNoLightForSwitch:
    async def test_light_platform_skips_switch_type(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state()
        added = await _run_light_setup(manager, mock_hass, mock_entry)
        assert added == []

    async def test_light_platform_skips_switch_override(
        self, manager: Control4Manager, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        manager._devices[IEEE_SWITCH] = _switch_state(device_type="keypaddim")
        manager.store._devices[IEEE_SWITCH] = DeviceConfig(
            ieee_address=IEEE_SWITCH,
            friendly_name="Bath Fan",
            device_type="keypaddim",
            device_type_override=DEVICE_TYPE_SWITCH,
        )
        added = await _run_light_setup(manager, mock_hass, mock_entry)
        assert added == []

    async def test_light_platform_still_serves_dimmers(
        self,
        manager: Control4Manager,
        mock_hass: MagicMock,
        mock_entry: MagicMock,
        dimmer_state: DeviceState,
    ) -> None:
        manager._devices[IEEE_DIMMER] = dimmer_state
        manager._devices[IEEE_SWITCH] = _switch_state()
        added = await _run_light_setup(manager, mock_hass, mock_entry)
        assert [e.unique_id for e in added] == [f"{IEEE_DIMMER}_dimmer"]

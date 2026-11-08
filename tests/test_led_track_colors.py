"""Tests for LEDs that track another entity's state."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.control4_dimmers.audit import slot_readings
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)

from .conftest import IEEE_DIMMER


def _tracked_slot(target: str = "light.living_room") -> SlotConfig:
    return SlotConfig(
        slot_id=2,
        behavior="keypad",
        led_mode="fixed",
        led_on_color="ffffff",
        led_off_color="0000ff",
        tap_action={"action": "light.turn_on", "target": {"entity_id": target}},
        led_track_entity_id=target,
    )


def _sent(manager: Control4Manager) -> list[str]:
    """Return the c4_cmd strings sent, in order."""
    return [
        c.args[1]["c4_cmd"]
        for c in manager.async_send_mqtt.call_args_list
        if "c4_cmd" in c.args[1]
    ]


def _target_state(manager: Control4Manager, state: str | None) -> None:
    """Make hass.states.get return a light in the given state (None = missing)."""
    if state is None:
        manager._hass.states.get = MagicMock(return_value=None)
        return
    target = MagicMock()
    target.state = state
    manager._hass.states.get = MagicMock(return_value=target)


def _setup(
    manager: Control4Manager, dimmer_state: DeviceState, slot: SlotConfig
) -> None:
    manager._devices[IEEE_DIMMER] = dimmer_state
    manager._store._devices[IEEE_DIMMER] = DeviceConfig(
        ieee_address=IEEE_DIMMER,
        friendly_name="Kitchen",
        device_type="dimmer",
        slots=[slot],
    )
    manager.async_send_mqtt = AsyncMock()


def _state_listener(manager: Control4Manager) -> Any:
    manager.setup_light_tracking()
    return manager._hass.bus.async_listen.call_args.args[1]


def _event(entity_id: str, state: str) -> MagicMock:
    new_state = MagicMock()
    new_state.state = state
    event = MagicMock()
    event.data = {"entity_id": entity_id, "new_state": new_state}
    return event


class TestTracksOtherEntity:
    """SlotConfig.tracks_other_entity."""

    def test_other_entity(self) -> None:
        assert _tracked_slot().tracks_other_entity

    def test_self_load_is_not_other(self) -> None:
        slot = SlotConfig(slot_id=2, led_track_entity_id="__self_load__")
        assert not slot.tracks_other_entity

    def test_untracked(self) -> None:
        assert not SlotConfig(slot_id=2).tracks_other_entity


class TestTrackingWritesLoadColors:
    """A load change on the device must not repaint a tracked LED."""

    @pytest.mark.asyncio
    async def test_state_change_writes_load_on_off_and_override(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup(manager, dimmer_state, _tracked_slot())
        listener = _state_listener(manager)
        await listener(_event("light.living_room", "on"))
        assert _sent(manager) == [
            "c4.dmx.led 01 05 ffffff",
            "c4.dmx.led 01 03 ffffff",
            "c4.dmx.led 01 04 ffffff",
        ]

    @pytest.mark.asyncio
    async def test_off_writes_the_off_color(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup(manager, dimmer_state, _tracked_slot())
        listener = _state_listener(manager)
        await listener(_event("light.living_room", "off"))
        assert _sent(manager) == [
            "c4.dmx.led 01 05 0000ff",
            "c4.dmx.led 01 03 0000ff",
            "c4.dmx.led 01 04 0000ff",
        ]

    @pytest.mark.asyncio
    async def test_self_load_still_writes_only_the_override(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        slot = SlotConfig(
            slot_id=2,
            led_on_color="ffffff",
            led_off_color="0000ff",
            led_track_entity_id="__self_load__",
        )
        _setup(manager, dimmer_state, slot)
        manager._find_light_entity = MagicMock(return_value="light.kitchen")
        listener = _state_listener(manager)
        await listener(_event("light.kitchen", "on"))
        assert _sent(manager) == ["c4.dmx.led 01 05 ffffff"]


class TestPushUsesTrackedColor:
    """A config push shows the tracked entity's current color."""

    @pytest.mark.asyncio
    async def test_push_writes_current_tracked_color(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        slot = _tracked_slot()
        _setup(manager, dimmer_state, slot)
        _target_state(manager, "on")
        await manager._send_slot_commands(dimmer_state, slot)
        colors = [c for c in _sent(manager) if c.split()[2] in ("03", "04", "05")]
        assert colors == [
            "c4.dmx.led 01 05 ffffff",
            "c4.dmx.led 01 03 ffffff",
            "c4.dmx.led 01 04 ffffff",
        ]

    @pytest.mark.asyncio
    async def test_missing_target_shows_off_color(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        slot = _tracked_slot()
        _setup(manager, dimmer_state, slot)
        _target_state(manager, None)
        await manager._send_slot_commands(dimmer_state, slot)
        assert "c4.dmx.led 01 05 0000ff" in _sent(manager)
        assert "c4.dmx.led 01 03 ffffff" not in _sent(manager)

    @pytest.mark.asyncio
    async def test_untracked_push_is_unchanged(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        slot = SlotConfig(
            slot_id=2, led_mode="fixed", led_on_color="ffffff", led_off_color="00ff00"
        )
        _setup(manager, dimmer_state, slot)
        await manager._send_slot_commands(dimmer_state, slot)
        colors = [c for c in _sent(manager) if c.split()[2] in ("03", "04", "05")]
        assert colors == [
            "c4.dmx.led 01 03 ffffff",
            "c4.dmx.led 01 04 00ff00",
            "c4.dmx.led 01 05 00ff00",
        ]


class TestAuditSkipsTrackedColors:
    """Runtime tracked colors are not drift from the stored colors."""

    def test_compare_slot_ignores_colors(self) -> None:
        mismatches = Control4Manager._compare_slot(
            _tracked_slot(),
            {"on": "0000ff", "off": "0000ff"},
            {"behavior": "keypad", "led_mode": "programmed"},
        )
        assert mismatches == []

    def test_compare_slot_still_checks_behavior(self) -> None:
        mismatches = Control4Manager._compare_slot(
            _tracked_slot(), {}, {"behavior": "toggle_load"}
        )
        assert mismatches == [("behavior", "keypad", "toggle_load")]

    def test_slot_readings_drop_colors(self) -> None:
        readings = slot_readings(_tracked_slot(), {}, {}, {"on": "0000ff"}, {})
        assert set(readings) == {"led_mode", "behavior"}

    def test_untracked_readings_keep_colors(self) -> None:
        readings = slot_readings(SlotConfig(slot_id=2), {}, {}, {}, {})
        assert set(readings) == {"on_color", "off_color", "led_mode", "behavior"}


def _pair() -> list[SlotConfig]:
    """Return a Top/Bottom pair tracking the same light, like Patio 2."""
    top = _tracked_slot()
    bottom = SlotConfig(
        slot_id=5,
        behavior="keypad",
        led_mode="fixed",
        led_on_color="000000",
        led_off_color="0000ff",
        tap_action={
            "action": "light.turn_off",
            "target": {"entity_id": "light.living_room"},
        },
        led_track_entity_id="light.living_room",
    )
    return [top, bottom]


def _setup_pair(manager: Control4Manager, dimmer_state: DeviceState) -> None:
    _setup(manager, dimmer_state, _tracked_slot())
    manager._store._devices[IEEE_DIMMER].slots = _pair()


class TestOptimisticLed:
    """A press updates every LED tracking the same light at once."""

    @pytest.mark.asyncio
    async def test_press_updates_both_leds_overrides_only(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup_pair(manager, dimmer_state)
        _target_state(manager, "off")
        pending = await manager.async_optimistic_led(IEEE_DIMMER, 2, "light.turn_on")
        assert _sent(manager) == [
            "c4.dmx.led 01 05 ffffff",
            "c4.dmx.led 04 05 000000",
        ]
        assert pending == [(1, "ffffff"), (4, "000000")]
        assert (IEEE_DIMMER, 5) in manager._led_cooldowns

    @pytest.mark.asyncio
    async def test_turn_on_while_on_keeps_the_on_color(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        # Pressing "on" when the light is already on must not flip the LED.
        _setup_pair(manager, dimmer_state)
        _target_state(manager, "on")
        await manager.async_optimistic_led(IEEE_DIMMER, 2, "light.turn_on")
        assert _sent(manager)[0] == "c4.dmx.led 01 05 ffffff"

    @pytest.mark.asyncio
    async def test_turn_off_predicts_off(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup_pair(manager, dimmer_state)
        _target_state(manager, "on")
        await manager.async_optimistic_led(IEEE_DIMMER, 5, "light.turn_off")
        # Both LEDs show their off colors (Top's is 0000ff in _tracked_slot).
        assert _sent(manager) == [
            "c4.dmx.led 01 05 0000ff",
            "c4.dmx.led 04 05 0000ff",
        ]

    @pytest.mark.asyncio
    async def test_toggle_predicts_the_opposite(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup(manager, dimmer_state, _tracked_slot())
        _target_state(manager, "off")
        await manager.async_optimistic_led(IEEE_DIMMER, 2, "light.toggle")
        assert _sent(manager) == ["c4.dmx.led 01 05 ffffff"]

    @pytest.mark.asyncio
    async def test_other_tracked_entity_is_left_alone(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup_pair(manager, dimmer_state)
        manager._store._devices[IEEE_DIMMER].slots[
            1
        ].led_track_entity_id = "light.other"
        _target_state(manager, "off")
        await manager.async_optimistic_led(IEEE_DIMMER, 2, "light.turn_on")
        assert _sent(manager) == ["c4.dmx.led 01 05 ffffff"]

    @pytest.mark.asyncio
    async def test_action_runs_between_overrides_and_load_colors(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup_pair(manager, dimmer_state)
        _target_state(manager, "off")
        order: list[str] = []
        manager.async_send_mqtt = AsyncMock(
            side_effect=lambda _ieee, payload: order.append(payload["c4_cmd"])
        )
        manager._hass.services.async_call = AsyncMock(
            side_effect=lambda *_args: order.append("action")
        )
        await manager.execute_slot_action(IEEE_DIMMER, 2, "tap")
        assert order == [
            "c4.dmx.led 01 05 ffffff",
            "c4.dmx.led 04 05 000000",
            "action",
            "c4.dmx.led 01 03 ffffff",
            "c4.dmx.led 01 04 ffffff",
            "c4.dmx.led 04 03 000000",
            "c4.dmx.led 04 04 000000",
        ]

    @pytest.mark.asyncio
    async def test_tracking_sends_all_overrides_first(
        self, manager: Control4Manager, dimmer_state: DeviceState
    ) -> None:
        _setup_pair(manager, dimmer_state)
        listener = _state_listener(manager)
        await listener(_event("light.living_room", "on"))
        assert _sent(manager) == [
            "c4.dmx.led 01 05 ffffff",
            "c4.dmx.led 04 05 000000",
            "c4.dmx.led 01 03 ffffff",
            "c4.dmx.led 01 04 ffffff",
            "c4.dmx.led 04 03 000000",
            "c4.dmx.led 04 04 000000",
        ]

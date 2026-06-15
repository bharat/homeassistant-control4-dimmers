"""Tests for the device-config service handlers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.control4_dimmers import (
    _svc_push_config,
    _svc_set_device_config,
    _svc_set_slot,
)
from custom_components.control4_dimmers.const import DOMAIN
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.store import Control4Store

IEEE = "0x000fff0000ccc001"


def _make_runtime(
    mock_hass: MagicMock,
    *,
    device_type: str = "keypad",
    config: DeviceConfig | None = None,
) -> tuple[Control4Manager, Control4Store]:
    """Build a real manager + store wired into hass.data, MQTT push stubbed."""
    entry = MagicMock()
    entry.data = {"mqtt_topic": "zigbee2mqtt"}
    entry.options = {}
    with patch("custom_components.control4_dimmers.store.Store"):
        store = Control4Store(mock_hass, "entry1")
    store._store.async_save = AsyncMock()
    if config is not None:
        store._devices[config.ieee_address] = config

    mgr = Control4Manager(mock_hass, entry, store)
    mgr._devices[IEEE] = DeviceState(
        ieee_address=IEEE,
        friendly_name="Theater",
        device_type=device_type,
    )
    mgr._push_slot_config = AsyncMock()
    mock_hass.data = {DOMAIN: {"entry1": {"manager": mgr, "store": store}}}
    mock_hass.states.async_all.return_value = []
    return mgr, store


def _call(data: dict[str, Any]) -> MagicMock:
    """Build a ServiceCall-like mock carrying the given data dict."""
    call = MagicMock()
    call.data = data
    return call


def _entity_state(ieee: str) -> MagicMock:
    state = MagicMock()
    state.attributes = {"ieee_address": ieee}
    return state


def _keypad_config() -> DeviceConfig:
    return DeviceConfig(
        ieee_address=IEEE,
        friendly_name="Theater",
        device_type="keypad",
        slots=[
            SlotConfig(slot_id=1, name="One", behavior="keypad", led_mode="fixed"),
            SlotConfig(slot_id=2, name="Two", behavior="keypad", led_mode="fixed"),
        ],
    )


# ── set_device_config ────────────────────────────────────────────────


class TestSetDeviceConfig:
    @pytest.mark.asyncio
    async def test_full_slot_replace(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        call = _call(
            {
                "ieee_address": IEEE,
                "slots": [
                    {"slot_id": 3, "name": "Three", "led_off_color": "#FF0000"},
                ],
            }
        )
        result = await _svc_set_device_config(mock_hass, call)
        config = store.get_device(IEEE)
        assert [s.slot_id for s in config.slots] == [3]
        assert config.slots[0].name == "Three"
        # The "#" is stripped and the hex lower-cased.
        assert config.slots[0].led_off_color == "ff0000"
        assert result["slots"][0]["slot_id"] == 3

    @pytest.mark.asyncio
    async def test_partial_only_faceplate_leaves_slots(
        self, mock_hass: MagicMock
    ) -> None:
        mgr, store = _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "faceplate_color": "abcdef"})
        await _svc_set_device_config(mock_hass, call)
        config = store.get_device(IEEE)
        # Slots untouched, faceplate set, and no firmware push happened.
        assert [s.slot_id for s in config.slots] == [1, 2]
        assert config.faceplate_color == "abcdef"
        mgr._push_slot_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_device_type_override_only(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "device_type_override": "keypaddim"})
        await _svc_set_device_config(mock_hass, call)
        config = store.get_device(IEEE)
        assert config.device_type_override == "keypaddim"
        assert [s.slot_id for s in config.slots] == [1, 2]

    @pytest.mark.asyncio
    async def test_slots_pushed_to_firmware(self, mock_hass: MagicMock) -> None:
        mgr, _ = _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slots": [{"slot_id": 1}]})
        await _svc_set_device_config(mock_hass, call)
        mgr._push_slot_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_illegal_slot_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slots": [{"slot_id": 99}]})
        with pytest.raises(ServiceValidationError):
            await _svc_set_device_config(mock_hass, call)

    @pytest.mark.asyncio
    async def test_override_used_for_slot_validation(
        self, mock_hass: MagicMock
    ) -> None:
        # Device detected as keypad (slots 1-6); override to dimmer (2,5)
        # in the same call should make slot 3 illegal.
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call(
            {
                "ieee_address": IEEE,
                "device_type_override": "dimmer",
                "slots": [{"slot_id": 3}],
            }
        )
        with pytest.raises(ServiceValidationError):
            await _svc_set_device_config(mock_hass, call)


# ── set_slot ─────────────────────────────────────────────────────────


class TestSetSlot:
    @pytest.mark.asyncio
    async def test_replace_existing_slot(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        call = _call(
            {
                "ieee_address": IEEE,
                "slot_id": 1,
                "name": "Renamed",
                "led_mode": "push_release",
            }
        )
        result = await _svc_set_slot(mock_hass, call)
        config = store.get_device(IEEE)
        slot1 = next(s for s in config.slots if s.slot_id == 1)
        assert slot1.name == "Renamed"
        assert slot1.led_mode == "push_release"
        # Other slots untouched.
        assert {s.slot_id for s in config.slots} == {1, 2}
        assert result["slots"]

    @pytest.mark.asyncio
    async def test_merge_one_field_keeps_others(self, mock_hass: MagicMock) -> None:
        cfg = _keypad_config()
        cfg.slots[0].led_off_color = "123456"
        cfg.slots[0].name = "Keep Me"
        _, store = _make_runtime(mock_hass, config=cfg)
        call = _call({"ieee_address": IEEE, "slot_id": 1, "led_on_color": "00ff00"})
        await _svc_set_slot(mock_hass, call)
        slot1 = next(s for s in store.get_device(IEEE).slots if s.slot_id == 1)
        assert slot1.led_on_color == "00ff00"
        # Untouched fields keep their prior values.
        assert slot1.led_off_color == "123456"
        assert slot1.name == "Keep Me"

    @pytest.mark.asyncio
    async def test_create_new_slot(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 4, "name": "Brand New"})
        await _svc_set_slot(mock_hass, call)
        config = store.get_device(IEEE)
        assert {s.slot_id for s in config.slots} == {1, 2, 4}
        new = next(s for s in config.slots if s.slot_id == 4)
        assert new.name == "Brand New"
        # Omitted fields fall back to dataclass defaults.
        assert new.behavior == "keypad"

    @pytest.mark.asyncio
    async def test_create_new_slot_no_existing_config(
        self, mock_hass: MagicMock
    ) -> None:
        _, store = _make_runtime(mock_hass, config=None)
        call = _call({"ieee_address": IEEE, "slot_id": 1, "name": "First"})
        await _svc_set_slot(mock_hass, call)
        config = store.get_device(IEEE)
        assert [s.slot_id for s in config.slots] == [1]

    @pytest.mark.asyncio
    async def test_illegal_slot_id_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 7})
        with pytest.raises(ServiceValidationError):
            await _svc_set_slot(mock_hass, call)

    @pytest.mark.asyncio
    async def test_bad_color_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 1, "led_on_color": "nothex"})
        with pytest.raises(ServiceValidationError):
            await _svc_set_slot(mock_hass, call)

    @pytest.mark.asyncio
    async def test_bad_led_mode_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 1, "led_mode": "strobe"})
        with pytest.raises(ServiceValidationError):
            await _svc_set_slot(mock_hass, call)

    @pytest.mark.asyncio
    async def test_bad_behavior_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 1, "behavior": "explode"})
        with pytest.raises(ServiceValidationError):
            await _svc_set_slot(mock_hass, call)

    @pytest.mark.asyncio
    async def test_action_dict_accepted(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        action = {"action": "light.toggle", "target": {"entity_id": "light.x"}}
        call = _call({"ieee_address": IEEE, "slot_id": 1, "tap_action": action})
        await _svc_set_slot(mock_hass, call)
        slot1 = next(s for s in store.get_device(IEEE).slots if s.slot_id == 1)
        assert slot1.tap_action == action

    @pytest.mark.asyncio
    async def test_garbage_action_rejected(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": IEEE, "slot_id": 1, "tap_action": {"foo": "bar"}})
        with pytest.raises(ServiceValidationError):
            await _svc_set_slot(mock_hass, call)


# ── push_config ──────────────────────────────────────────────────────


class TestPushConfig:
    @pytest.mark.asyncio
    async def test_pushes_without_mutating(self, mock_hass: MagicMock) -> None:
        cfg = _keypad_config()
        mgr, store = _make_runtime(mock_hass, config=cfg)
        before = store.get_device(IEEE).to_dict()
        result = await _svc_push_config(mock_hass, _call({"ieee_address": IEEE}))
        mgr._push_slot_config.assert_awaited_once()
        assert result == {"pushed": True, "ieee_address": IEEE}
        # Stored config is unchanged by a push.
        assert store.get_device(IEEE).to_dict() == before

    @pytest.mark.asyncio
    async def test_returns_false_without_stored_config(
        self, mock_hass: MagicMock
    ) -> None:
        mgr, _ = _make_runtime(mock_hass, config=None)
        result = await _svc_push_config(mock_hass, _call({"ieee_address": IEEE}))
        assert result == {"pushed": False, "ieee_address": IEEE}
        mgr._push_slot_config.assert_not_awaited()


# ── device resolution ────────────────────────────────────────────────


class TestDeviceResolution:
    @pytest.mark.asyncio
    async def test_entity_id_resolves(self, mock_hass: MagicMock) -> None:
        _, store = _make_runtime(mock_hass, config=_keypad_config())
        mock_hass.states.get.return_value = _entity_state(IEEE)
        call = _call({"entity_id": "event.theater_button_1", "slot_id": 1})
        await _svc_set_slot(mock_hass, call)
        assert store.get_device(IEEE) is not None

    @pytest.mark.asyncio
    async def test_ieee_address_resolves(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        result = await _svc_push_config(mock_hass, _call({"ieee_address": IEEE}))
        assert result["ieee_address"] == IEEE

    @pytest.mark.asyncio
    async def test_neither_id_errors(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        with pytest.raises(ServiceValidationError):
            await _svc_push_config(mock_hass, _call({}))

    @pytest.mark.asyncio
    async def test_both_ids_error(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        mock_hass.states.get.return_value = _entity_state(IEEE)
        call = _call({"entity_id": "event.x", "ieee_address": IEEE})
        with pytest.raises(ServiceValidationError):
            await _svc_push_config(mock_hass, call)

    @pytest.mark.asyncio
    async def test_unknown_device_errors(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        call = _call({"ieee_address": "0xdeadbeef"})
        with pytest.raises(ServiceValidationError):
            await _svc_push_config(mock_hass, call)

    @pytest.mark.asyncio
    async def test_entity_without_ieee_errors(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        state = MagicMock()
        state.attributes = {}
        mock_hass.states.get.return_value = state
        with pytest.raises(ServiceValidationError):
            await _svc_push_config(mock_hass, _call({"entity_id": "event.x"}))

    @pytest.mark.asyncio
    async def test_missing_entity_errors(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        mock_hass.states.get.return_value = None
        with pytest.raises(ServiceValidationError):
            await _svc_push_config(mock_hass, _call({"entity_id": "event.gone"}))


# ── response payload shape ───────────────────────────────────────────


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_set_device_config_returns_config_dict(
        self, mock_hass: MagicMock
    ) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        result = await _svc_set_device_config(
            mock_hass, _call({"ieee_address": IEEE, "faceplate_color": "ffffff"})
        )
        assert result["ieee_address"] == IEEE
        assert isinstance(result["slots"], list)

    @pytest.mark.asyncio
    async def test_set_slot_returns_config_dict(self, mock_hass: MagicMock) -> None:
        _make_runtime(mock_hass, config=_keypad_config())
        result = await _svc_set_slot(
            mock_hass, _call({"ieee_address": IEEE, "slot_id": 1})
        )
        assert result["ieee_address"] == IEEE
        assert isinstance(result["slots"], list)

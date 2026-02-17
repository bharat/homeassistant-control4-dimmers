"""Tests for data models (SlotConfig, DeviceConfig, DeviceState)."""

from __future__ import annotations

import pytest

from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
    _extract_color,
    _hs_to_hex,
)

# ── SlotConfig ───────────────────────────────────────────────────────


class TestSlotConfig:
    """Tests for SlotConfig dataclass."""

    def test_defaults(self) -> None:
        slot = SlotConfig(slot_id=0)
        assert slot.size == 1
        assert slot.name == ""
        assert slot.behavior == "keypad"
        assert slot.led_mode == "programmed"
        assert slot.led_on_color == "0000ff"
        assert slot.led_off_color == "000000"

    def test_to_dict(self) -> None:
        slot = SlotConfig(slot_id=3, name="Dining", led_on_color="ff0000")
        d = slot.to_dict()
        assert d["slot_id"] == 3
        assert d["name"] == "Dining"
        assert d["led_on_color"] == "ff0000"
        assert "behavior" in d

    def test_from_dict_minimal(self) -> None:
        slot = SlotConfig.from_dict({"slot_id": 2})
        assert slot.slot_id == 2
        assert slot.name == ""

    def test_from_dict_full(self) -> None:
        data = {
            "slot_id": 5,
            "size": 2,
            "name": "Master",
            "behavior": "toggle",
            "led_mode": "manual",
            "led_on_color": "00ff00",
            "led_off_color": "330000",
        }
        slot = SlotConfig.from_dict(data)
        assert slot.slot_id == 5
        assert slot.size == 2
        assert slot.name == "Master"
        assert slot.behavior == "toggle"

    def test_roundtrip(self) -> None:
        original = SlotConfig(slot_id=1, name="Top", led_on_color="ffffff")
        rebuilt = SlotConfig.from_dict(original.to_dict())
        assert rebuilt == original


# ── DeviceConfig ─────────────────────────────────────────────────────


class TestDeviceConfig:
    """Tests for DeviceConfig dataclass."""

    def test_effective_type_uses_override(self) -> None:
        config = DeviceConfig(
            ieee_address="0x001",
            friendly_name="Test",
            device_type="dimmer",
            device_type_override="keypaddim",
        )
        assert config.effective_type == "keypaddim"

    def test_effective_type_falls_back_to_detected(self) -> None:
        config = DeviceConfig(
            ieee_address="0x001",
            friendly_name="Test",
            device_type="keypad",
        )
        assert config.effective_type == "keypad"

    def test_effective_type_empty_when_none(self) -> None:
        config = DeviceConfig(
            ieee_address="0x001",
            friendly_name="Test",
        )
        assert config.effective_type == ""

    def test_to_dict_includes_slots(self) -> None:
        config = DeviceConfig(
            ieee_address="0x001",
            friendly_name="Test",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=0), SlotConfig(slot_id=1)],
        )
        d = config.to_dict()
        assert len(d["slots"]) == 2
        assert d["slots"][0]["slot_id"] == 0

    def test_from_dict_no_slots(self) -> None:
        data = {"ieee_address": "0x001", "friendly_name": "Test"}
        config = DeviceConfig.from_dict(data)
        assert config.slots == []
        assert config.device_type == ""

    def test_roundtrip(self, dimmer_config: DeviceConfig) -> None:
        rebuilt = DeviceConfig.from_dict(dimmer_config.to_dict())
        assert rebuilt.ieee_address == dimmer_config.ieee_address
        assert rebuilt.friendly_name == dimmer_config.friendly_name
        assert len(rebuilt.slots) == len(dimmer_config.slots)
        assert rebuilt.slots[0].name == dimmer_config.slots[0].name


# ── DeviceState ──────────────────────────────────────────────────────


class TestDeviceState:
    """Tests for DeviceState and MQTT update logic."""

    def test_update_basic_state(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt({"state": "ON", "brightness": 128})
        assert state.state == "ON"
        assert state.brightness == 128

    def test_update_device_type(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt({"c4_device_type": "keypaddim"})
        assert state.device_type == "keypaddim"

    def test_update_led_colors(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt(
            {
                "color_button_0_on": {"hue": 240, "saturation": 100},
                "color_button_0_off": "000000",
            }
        )
        assert 0 in state.led_colors
        assert "on" in state.led_colors[0]
        assert state.led_colors[0]["off"] == "000000"

    def test_update_button_configs(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt(
            {
                "button_2_behavior": "toggle",
                "button_2_led_mode": "manual",
            }
        )
        assert state.button_configs[2]["behavior"] == "toggle"
        assert state.button_configs[2]["led_mode"] == "manual"

    def test_update_preserves_raw(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt({"linkquality": 200, "state": "OFF"})
        assert state.raw["linkquality"] == 200
        assert state.raw["state"] == "OFF"

    def test_update_partial_does_not_clobber(self) -> None:
        state = DeviceState(ieee_address="0x001", friendly_name="Test")
        state.update_from_mqtt({"state": "ON", "brightness": 200})
        state.update_from_mqtt({"brightness": 50})
        assert state.state == "ON"
        assert state.brightness == 50


# ── Color helpers ────────────────────────────────────────────────────


class TestColorHelpers:
    """Tests for _extract_color and _hs_to_hex."""

    def test_extract_color_from_string(self) -> None:
        assert _extract_color("#ff0000") == "ff0000"
        assert _extract_color("00ff00") == "00ff00"

    def test_extract_color_from_dict(self) -> None:
        result = _extract_color({"hue": 0, "saturation": 100})
        assert result == "ff0000"

    def test_extract_color_fallback(self) -> None:
        assert _extract_color(42) == "000000"
        assert _extract_color(None) == "000000"

    def test_hs_to_hex_red(self) -> None:
        assert _hs_to_hex(0, 100) == "ff0000"

    def test_hs_to_hex_green(self) -> None:
        assert _hs_to_hex(120, 100) == "00ff00"

    def test_hs_to_hex_blue(self) -> None:
        assert _hs_to_hex(240, 100) == "0000ff"

    def test_hs_to_hex_white(self) -> None:
        assert _hs_to_hex(0, 0) == "ffffff"

    @pytest.mark.parametrize(
        ("hue", "saturation"),
        [(60, 100), (180, 100), (300, 100), (30, 50)],
    )
    def test_hs_to_hex_valid_range(self, hue: float, saturation: float) -> None:
        result = _hs_to_hex(hue, saturation)
        assert len(result) == 6
        int(result, 16)  # must be valid hex

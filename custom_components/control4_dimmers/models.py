"""Data models for Control4 Dimmers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SlotConfig:
    """Configuration for a single button slot."""

    slot_id: int
    size: int = 1
    name: str = ""
    behavior: str = "keypad"
    led_mode: str = "programmed"
    led_on_color: str = "0000ff"
    led_off_color: str = "000000"
    target_entity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        d = {
            "slot_id": self.slot_id,
            "size": self.size,
            "name": self.name,
            "behavior": self.behavior,
            "led_mode": self.led_mode,
            "led_on_color": self.led_on_color,
            "led_off_color": self.led_off_color,
        }
        if self.target_entity_id:
            d["target_entity_id"] = self.target_entity_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotConfig:
        """Deserialize from dict."""
        return cls(
            slot_id=data["slot_id"],
            size=data.get("size", 1),
            name=data.get("name", ""),
            behavior=data.get("behavior", "keypad"),
            led_mode=data.get("led_mode", "programmed"),
            led_on_color=data.get("led_on_color", "0000ff"),
            led_off_color=data.get("led_off_color", "000000"),
            target_entity_id=data.get("target_entity_id"),
        )


@dataclass
class DeviceConfig:
    """Persisted configuration for a Control4 device."""

    ieee_address: str
    friendly_name: str
    device_type: str = ""
    device_type_override: str | None = None
    slots: list[SlotConfig] = field(default_factory=list)

    @property
    def effective_type(self) -> str:
        """Return override type if set, otherwise detected type."""
        return self.device_type_override or self.device_type

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "ieee_address": self.ieee_address,
            "friendly_name": self.friendly_name,
            "device_type": self.device_type,
            "device_type_override": self.device_type_override,
            "slots": [s.to_dict() for s in self.slots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceConfig:
        """Deserialize from dict."""
        return cls(
            ieee_address=data["ieee_address"],
            friendly_name=data["friendly_name"],
            device_type=data.get("device_type", ""),
            device_type_override=data.get("device_type_override"),
            slots=[SlotConfig.from_dict(s) for s in data.get("slots", [])],
        )


@dataclass
class DeviceState:
    """Live state of a Control4 device from Z2M MQTT."""

    ieee_address: str
    friendly_name: str
    model_id: str = ""
    device_type: str | None = None
    available: bool = True
    brightness: int | None = None
    state: str | None = None
    led_colors: dict[int, dict[str, str]] = field(default_factory=dict)
    button_configs: dict[int, dict[str, str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def update_from_mqtt(self, payload: dict[str, Any]) -> None:
        """Update state from an MQTT state payload."""
        self.raw.update(payload)

        if "state" in payload:
            self.state = payload["state"]
        if "brightness" in payload:
            self.brightness = payload["brightness"]
        if "c4_device_type" in payload:
            self.device_type = payload["c4_device_type"]

        for btn in range(1, 7):
            # Flat hex LED attributes from slim Z2M converter
            led_on_key = f"c4_led_{btn}_on"
            led_off_key = f"c4_led_{btn}_off"
            if led_on_key in payload:
                self.led_colors.setdefault(btn, {})["on"] = _extract_color(
                    payload[led_on_key]
                )
            if led_off_key in payload:
                self.led_colors.setdefault(btn, {})["off"] = _extract_color(
                    payload[led_off_key]
                )

            # Legacy Z2M HS color format (for backwards compatibility)
            color_on_key = f"color_button_{btn}_on"
            color_off_key = f"color_button_{btn}_off"
            if color_on_key in payload:
                self.led_colors.setdefault(btn, {})["on"] = _extract_color(
                    payload[color_on_key]
                )
            if color_off_key in payload:
                self.led_colors.setdefault(btn, {})["off"] = _extract_color(
                    payload[color_off_key]
                )

            beh_key = f"button_{btn}_behavior"
            mode_key = f"button_{btn}_led_mode"
            if beh_key in payload:
                self.button_configs.setdefault(btn, {})["behavior"] = payload[beh_key]
            if mode_key in payload:
                self.button_configs.setdefault(btn, {})["led_mode"] = payload[mode_key]


def _extract_color(color_data: Any) -> str:
    """Extract RGB hex string from Z2M color payload."""
    if isinstance(color_data, str):
        return color_data.lstrip("#")
    if isinstance(color_data, dict):
        h = color_data.get("hue", 0)
        s = color_data.get("saturation", 0)
        return _hs_to_hex(h, s)
    return "000000"


def _hs_to_hex(hue: float, saturation: float) -> str:
    """Convert hue/saturation to RGB hex (full brightness)."""
    h = hue / 360.0
    s = saturation / 100.0
    v = 1.0
    if s == 0.0:
        r = g = b = int(v * 255)
        return f"{r:02x}{g:02x}{b:02x}"

    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6

    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:  # noqa: PLR2004
        r, g, b = p, v, t
    elif i == 3:  # noqa: PLR2004
        r, g, b = p, q, v
    elif i == 4:  # noqa: PLR2004
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q

    return f"{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

"""
Pure helpers for the periodic config audit sweep (issue #199).

The manager owns the MQTT round-trips; everything here is side-effect
free so the drift classification and the "stalest device first" ordering
can be unit tested without a device.

Three layers can hold a button slot's configuration:

- store: the integration's persisted intent (SlotConfig).
- cache: Zigbee2MQTT's cached button_N_behavior / button_N_led_mode /
  c4_led_N_* state. The converter's smart-behavior path consults the
  cached behavior on every press, so a stale cache misbehaves even when
  the firmware is right. The integration sees this layer as a mirror:
  Z2M publishes its full cached state with every device message and
  DeviceState keeps the latest copy.
- firmware: what the device itself answers to a read-back query.

An audit compares all three per field and names the layer that diverged
from the store, so the log tells you where to look instead of just that
something is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import DRIFT_LAYER_BOTH, DRIFT_LAYER_CACHE, DRIFT_LAYER_FIRMWARE

if TYPE_CHECKING:
    from datetime import datetime

    from .models import SlotConfig


@dataclass(frozen=True)
class AuditMismatch:
    """One field whose stored intent is not what a layer holds."""

    field: str
    store: str
    cache: str | None
    firmware: str | None
    layer: str


@dataclass(frozen=True)
class FieldReading:
    """The three values of one field, or None where a layer has no value."""

    store: str
    cache: str | None
    firmware: str | None


def _norm_color(value: str | None) -> str | None:
    """Normalize a hex color for comparison (no #, lowercase)."""
    if value is None:
        return None
    return value.lstrip("#").lower()


def firmware_led_mode(slot: SlotConfig) -> str:
    """Map the integration's led_mode name to the firmware/Z2M name."""
    return "programmed" if slot.led_mode == "fixed" else slot.led_mode


def slot_readings(
    slot: SlotConfig,
    cached_led: dict[str, str],
    cached_btn: dict[str, str],
    fw_led: dict[str, str],
    fw_btn: dict[str, str],
) -> dict[str, FieldReading]:
    """Assemble per-field (store, cache, firmware) readings for a slot."""
    return {
        "on_color": FieldReading(
            _norm_color(slot.led_on_color) or "",
            _norm_color(cached_led.get("on")),
            _norm_color(fw_led.get("on")),
        ),
        "off_color": FieldReading(
            _norm_color(slot.led_off_color) or "",
            _norm_color(cached_led.get("off")),
            _norm_color(fw_led.get("off")),
        ),
        "led_mode": FieldReading(
            firmware_led_mode(slot),
            cached_btn.get("led_mode"),
            fw_btn.get("led_mode"),
        ),
        "behavior": FieldReading(
            slot.behavior,
            cached_btn.get("behavior"),
            fw_btn.get("behavior"),
        ),
    }


def classify_drift(reading: FieldReading) -> str | None:
    """
    Name the layer that diverged from the store for one field.

    Returns None when nothing readable disagrees with the store. A layer
    with no value (None) cannot be judged and is skipped, so a field the
    firmware could not read is compared against the cache alone and vice
    versa. A field with neither layer present is unreadable, not drifted.
    """
    store, cache, fw = reading.store, reading.cache, reading.firmware
    cache_ok = cache is None or cache == store
    fw_ok = fw is None or fw == store
    if cache_ok and fw_ok:
        return None
    if cache_ok:
        return DRIFT_LAYER_FIRMWARE
    if fw_ok:
        return DRIFT_LAYER_CACHE
    return DRIFT_LAYER_BOTH


def compare_three_way(
    slot: SlotConfig,
    cached_led: dict[str, str],
    cached_btn: dict[str, str],
    fw_led: dict[str, str],
    fw_btn: dict[str, str],
) -> list[AuditMismatch]:
    """Compare the store against the cache and firmware for every field."""
    mismatches: list[AuditMismatch] = []
    for field, reading in slot_readings(
        slot, cached_led, cached_btn, fw_led, fw_btn
    ).items():
        layer = classify_drift(reading)
        if layer is not None:
            mismatches.append(
                AuditMismatch(
                    field=field,
                    store=reading.store,
                    cache=reading.cache,
                    firmware=reading.firmware,
                    layer=layer,
                )
            )
    return mismatches


def summarize_layers(mismatches: list[AuditMismatch]) -> str | None:
    """Collapse per-field layers into one slot-level verdict."""
    if not mismatches:
        return None
    layers = {m.layer for m in mismatches}
    if len(layers) == 1:
        return next(iter(layers))
    return DRIFT_LAYER_BOTH


def pick_stalest(
    candidates: dict[str, datetime | None],
) -> str | None:
    """
    Choose the device to audit next.

    candidates maps an IEEE to its effective verification time: None for a
    device that has never been fully verified (or attempted), otherwise
    the oldest of its slots' last_verified, floored at the device's last
    audit attempt so an unreachable device cannot monopolize the sweep.
    Never-verified devices go first; ties break on IEEE for determinism.
    """
    if not candidates:
        return None

    def _key(item: tuple[str, datetime | None]) -> tuple[int, float, str]:
        ieee, when = item
        if when is None:
            return (0, 0.0, ieee)
        return (1, when.timestamp(), ieee)

    return min(candidates.items(), key=_key)[0]

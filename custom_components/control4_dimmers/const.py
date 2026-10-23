"""Constants for Control4 Dimmers."""

from __future__ import annotations

import json
from contextlib import suppress
from logging import Logger, getLogger
from pathlib import Path
from typing import Final

LOGGER: Logger = getLogger(__package__)

DOMAIN: Final = "control4_dimmers"

MANIFEST_PATH = Path(__file__).parent / "manifest.json"
FRONTEND_CARD_PATH = Path(__file__).parent / "frontend" / "control4-dimmer-card.js"

with MANIFEST_PATH.open(encoding="utf-8") as _manifest_file:
    _version = json.load(_manifest_file).get("version", "0.0.0")

if _version == "0.0.0":
    with suppress(FileNotFoundError):
        _version = str(int(FRONTEND_CARD_PATH.stat().st_mtime))

INTEGRATION_VERSION: Final[str] = _version

URL_BASE: Final = f"/{DOMAIN}"
JSMODULES: Final[list[dict[str, str]]] = [
    {
        "name": "Control4 Dimmer Card",
        "filename": "control4-dimmer-card.js",
        "version": INTEGRATION_VERSION,
    }
]

CONF_MQTT_TOPIC: Final = "mqtt_topic"
DEFAULT_MQTT_TOPIC: Final = "zigbee2mqtt"

# Periodic config audit sweep (issue #199). Every CONF_AUDIT_INTERVAL
# minutes the manager picks the device whose stored config has gone the
# longest without a read-back verify and verifies every one of its slots.
# 0 disables the sweep. CONF_AUDIT_MODE selects whether a mismatch is
# re-pushed (heal) or only logged (report).
CONF_AUDIT_INTERVAL: Final = "audit_interval"
DEFAULT_AUDIT_INTERVAL: Final = 10
MAX_AUDIT_INTERVAL: Final = 1440
CONF_AUDIT_MODE: Final = "audit_mode"
AUDIT_MODE_HEAL: Final = "heal"
AUDIT_MODE_REPORT: Final = "report"
AUDIT_MODES: Final = [AUDIT_MODE_HEAL, AUDIT_MODE_REPORT]
DEFAULT_AUDIT_MODE: Final = AUDIT_MODE_HEAL

# Which layer an audit found diverged from the stored intent. "cache" is
# Z2M's cached button_N_* state (what the converter's smart-behavior path
# consults on a press), "firmware" is the value read back from the device,
# "both" means both layers disagree with the store.
DRIFT_LAYER_CACHE: Final = "cache"
DRIFT_LAYER_FIRMWARE: Final = "firmware"
DRIFT_LAYER_BOTH: Final = "both"

STORAGE_KEY: Final = "control4_dimmers_devices"
SNAPSHOT_STORAGE_KEY: Final = "control4_dimmers_snapshots"
STORAGE_VERSION: Final = 1

C4_MANUFACTURER_NAME: Final = "Control4"

DEVICE_TYPE_DIMMER: Final = "dimmer"
DEVICE_TYPE_KEYPADDIM: Final = "keypaddim"
DEVICE_TYPE_KEYPAD: Final = "keypad"
DEVICE_TYPES: Final = [DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM, DEVICE_TYPE_KEYPAD]

# Device types that drive a local load and can therefore have a 3-way
# auxiliary switch wired to them (issue #117). Pure keypads have no load and
# get no auxiliary entities.
LOAD_BEARING_TYPES: Final = [DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM]

SLOT_COUNT: Final = 6

# Halves of a 3-way auxiliary switch: the non-Zigbee switch wired to the
# device's traveler terminal so two switches control one load. The device
# reports its presses on a wire-id space (bp 07/08) distinct from the
# button-array slots; Z2M exposes them as paddle_up / paddle_down (frozen
# MQTT contract names). The device's own rocker reports as button slots.
PADDLE_UP: Final = "paddle_up"
PADDLE_DOWN: Final = "paddle_down"
PADDLE_IDS: Final[list[str]] = [PADDLE_UP, PADDLE_DOWN]
PADDLE_NAMES: Final[dict[str, str]] = {
    PADDLE_UP: "Auxiliary Up",
    PADDLE_DOWN: "Auxiliary Down",
}

DEVICE_TYPE_SLOTS: Final[dict[str, list[int]]] = {
    DEVICE_TYPE_DIMMER: [2, 5],
    DEVICE_TYPE_KEYPADDIM: [1, 2, 3, 4, 5, 6],
    DEVICE_TYPE_KEYPAD: [1, 2, 3, 4, 5, 6],
}

BUTTON_EVENT_TYPES: Final[list[str]] = [
    "pressed",
    "released",
    "single_tap",
    "double_tap",
    "triple_tap",
]

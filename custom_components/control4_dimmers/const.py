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
FRONTEND_CARD_PATH = Path(__file__).parent / "frontend" / "control4-card.js"

with MANIFEST_PATH.open(encoding="utf-8") as _manifest_file:
    _version = json.load(_manifest_file).get("version", "0.0.0")

if _version == "0.0.0":
    with suppress(FileNotFoundError):
        _version = str(int(FRONTEND_CARD_PATH.stat().st_mtime))

INTEGRATION_VERSION: Final[str] = _version

URL_BASE: Final = f"/{DOMAIN}"
JSMODULES: Final[list[dict[str, str]]] = [
    {
        "name": "Control4 Card",
        "filename": "control4-card.js",
        "version": INTEGRATION_VERSION,
    }
]

CONF_MQTT_TOPIC: Final = "mqtt_topic"
DEFAULT_MQTT_TOPIC: Final = "zigbee2mqtt"

STORAGE_KEY: Final = "control4_dimmers_devices"
STORAGE_VERSION: Final = 1

C4_MANUFACTURER_ID: Final = 43981
C4_MANUFACTURER_NAME: Final = "Control4"

DEVICE_TYPE_DIMMER: Final = "dimmer"
DEVICE_TYPE_KEYPADDIM: Final = "keypaddim"
DEVICE_TYPE_KEYPAD: Final = "keypad"
DEVICE_TYPES: Final = [DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM, DEVICE_TYPE_KEYPAD]

DEVICE_MODELS: Final = {
    DEVICE_TYPE_DIMMER: "C4-APD120",
    DEVICE_TYPE_KEYPADDIM: "C4-KD120",
    DEVICE_TYPE_KEYPAD: "C4-KC120277",
}

BUTTON_BEHAVIORS: Final = ["keypad", "toggle_load", "load_on", "load_off"]
LED_MODES: Final = [
    "follow_load",
    "follow_connection",
    "push_release",
    "programmed",
]

SLOT_COUNT: Final = 6

DIMMER_ACTIVE_SLOTS: Final = {1, 4}
KEYPAD_ACTIVE_SLOTS: Final = {0, 1, 2, 3, 4, 5}

SERVICE_CONFIGURE_SLOTS: Final = "configure_slots"
SERVICE_SET_LED_COLOR: Final = "set_led_color"
SERVICE_DETECT_DEVICE: Final = "detect_device"

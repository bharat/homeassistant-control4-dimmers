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

STORAGE_KEY: Final = "control4_dimmers_devices"
SNAPSHOT_STORAGE_KEY: Final = "control4_dimmers_snapshots"
STORAGE_VERSION: Final = 1

C4_MANUFACTURER_NAME: Final = "Control4"

DEVICE_TYPE_DIMMER: Final = "dimmer"
DEVICE_TYPE_KEYPADDIM: Final = "keypaddim"
DEVICE_TYPE_KEYPAD: Final = "keypad"
DEVICE_TYPES: Final = [DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM, DEVICE_TYPE_KEYPAD]

# Device types that drive a local load and therefore have a physical load
# paddle (issue #117). Pure keypads have no load and get no paddle entities.
LOAD_BEARING_TYPES: Final = [DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM]

SLOT_COUNT: Final = 6

# Local load paddle halves. These live on a wire-id space (bp 07/08) distinct
# from the button-array slots and are exposed as paddle_up / paddle_down.
PADDLE_UP: Final = "paddle_up"
PADDLE_DOWN: Final = "paddle_down"
PADDLE_IDS: Final[list[str]] = [PADDLE_UP, PADDLE_DOWN]
PADDLE_NAMES: Final[dict[str, str]] = {
    PADDLE_UP: "Paddle Up",
    PADDLE_DOWN: "Paddle Down",
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

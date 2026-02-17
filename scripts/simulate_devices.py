#!/usr/bin/env python3
"""
Simulate Control4 Z2M devices for development.

Publishes fake Z2M MQTT messages for 6 Control4 devices (2 of each type)
so the custom integration can be developed and tested without real hardware.

Usage:
    python3 scripts/simulate_devices.py [--broker localhost] [--port 1883] [--topic zigbee2mqtt]

Requires: paho-mqtt
    pip install paho-mqtt
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time

try:
    import paho.mqtt.client as mqtt_client
except ImportError:
    print("Error: paho-mqtt is required. Install with: pip install paho-mqtt")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("c4-simulator")

# ─── Simulated devices ───

DEVICES = [
    {
        "ieee_address": "0x000fff0000aaa001",
        "friendly_name": "Kitchen Dimmer",
        "model_id": "C4-APD120",
        "type": "dimmer",
        "definition": {
            "model": "C4-APD120",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Adaptive Phase Dimmer",
        },
    },
    {
        "ieee_address": "0x000fff0000aaa002",
        "friendly_name": "Living Room Dimmer",
        "model_id": "C4-APD120",
        "type": "dimmer",
        "definition": {
            "model": "C4-APD120",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Adaptive Phase Dimmer",
        },
    },
    {
        "ieee_address": "0x000fff0000bbb001",
        "friendly_name": "Master Bedroom",
        "model_id": "C4-KD120",
        "type": "keypaddim",
        "definition": {
            "model": "C4-KD120",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Keypad Dimmer",
        },
    },
    {
        "ieee_address": "0x000fff0000bbb002",
        "friendly_name": "Dining Room",
        "model_id": "C4-KD120",
        "type": "keypaddim",
        "definition": {
            "model": "C4-KD120",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Keypad Dimmer",
        },
    },
    {
        "ieee_address": "0x000fff0000ccc001",
        "friendly_name": "Theater Keypad",
        "model_id": "C4-KC120277",
        "type": "keypad",
        "definition": {
            "model": "C4-KC120277",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Configurable Keypad",
        },
    },
    {
        "ieee_address": "0x000fff0000ccc002",
        "friendly_name": "Garage Keypad",
        "model_id": "C4-KC120277",
        "type": "keypad",
        "definition": {
            "model": "C4-KC120277",
            "vendor": "Control4",
            "manufacturer": "Control4",
            "description": "Configurable Keypad",
        },
    },
]

DIMMER_LED_DEFAULTS = {
    1: {"on": "ffffff", "off": "000000"},
    4: {"on": "000000", "off": "0000ff"},
}

KEYPAD_LED_DEFAULTS = {
    0: {"on": "0000cc", "off": "000033"},
    1: {"on": "0000cc", "off": "000033"},
    2: {"on": "0000cc", "off": "000033"},
    3: {"on": "00cc00", "off": "003300"},
    4: {"on": "cc0000", "off": "330000"},
    5: {"on": "cccc00", "off": "333300"},
}


def build_bridge_devices(devices: list[dict]) -> list[dict]:
    """Build the zigbee2mqtt/bridge/devices payload."""
    result = []
    for dev in devices:
        result.append(
            {
                "ieee_address": dev["ieee_address"],
                "friendly_name": dev["friendly_name"],
                "model_id": dev["model_id"],
                "manufacturer": "Control4",
                "type": "EndDevice",
                "network_address": random.randint(1000, 65000),
                "supported": True,
                "disabled": False,
                "definition": dev["definition"],
                "endpoints": {
                    "1": {
                        "bindings": [],
                        "configured_reportings": [],
                        "clusters": {"input": ["genOnOff", "genLevelCtrl"], "output": []},
                    },
                    "196": {"bindings": [], "configured_reportings": [], "clusters": {"input": [], "output": []}},
                    "197": {"bindings": [], "configured_reportings": [], "clusters": {"input": [], "output": []}},
                },
            }
        )
    return result


def build_device_state(dev: dict) -> dict:
    """Build a realistic state payload for a device."""
    device_type = dev["type"]
    state = {
        "c4_device_type": device_type,
        "linkquality": random.randint(60, 255),
    }

    if device_type in ("dimmer", "keypaddim"):
        brightness = random.choice([0, 64, 128, 200, 254])
        state["state"] = "ON" if brightness > 0 else "OFF"
        state["brightness"] = brightness

    leds = DIMMER_LED_DEFAULTS if device_type == "dimmer" else KEYPAD_LED_DEFAULTS
    for btn_id, colors in leds.items():
        for mode in ("on", "off"):
            color_hex = colors[mode]
            h, s = _hex_to_hs(color_hex)
            state[f"state_button_{btn_id}_{mode}"] = "ON"
            state[f"brightness_button_{btn_id}_{mode}"] = 254
            state[f"color_button_{btn_id}_{mode}"] = {"hue": h, "saturation": s}
            state[f"color_mode_button_{btn_id}_{mode}"] = "hs"

    for btn_id in (leds.keys() if device_type == "dimmer" else range(6)):
        state[f"button_{btn_id}_behavior"] = "keypad"
        state[f"button_{btn_id}_led_mode"] = "programmed"

    return state


def _hex_to_hs(hex_color: str) -> tuple[float, float]:
    """Convert hex RGB to hue/saturation."""
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    diff = mx - mn
    if diff == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360
    s = 0.0 if mx == 0 else (diff / mx) * 100
    return round(h, 1), round(s, 1)


class C4Simulator:
    """MQTT-based Control4 device simulator."""

    def __init__(self, broker: str, port: int, topic: str) -> None:
        self.broker = broker
        self.port = port
        self.topic = topic
        self.client = mqtt_client.Client(
            client_id="c4-simulator",
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
        )
        self.device_states: dict[str, dict] = {}

        for dev in DEVICES:
            self.device_states[dev["friendly_name"]] = build_device_state(dev)

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        log.info("Connected to MQTT broker at %s:%d", self.broker, self.port)
        client.subscribe(f"{self.topic}/+/set")
        log.info("Subscribed to %s/+/set", self.topic)

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        if not topic.endswith("/set"):
            return
        device_name = topic[len(self.topic) + 1 : -4]
        state = self.device_states.get(device_name)
        if state is None:
            return

        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, ValueError):
            return

        log.info("SET %s: %s", device_name, json.dumps(payload, indent=None))

        for key, value in payload.items():
            state[key] = value

        self.publish_state(device_name)

    def publish_bridge_devices(self):
        payload = json.dumps(build_bridge_devices(DEVICES))
        self.client.publish(
            f"{self.topic}/bridge/devices", payload, retain=True
        )
        log.info("Published bridge/devices with %d Control4 devices", len(DEVICES))

    def publish_state(self, device_name: str):
        state = self.device_states.get(device_name)
        if state is None:
            return
        self.client.publish(
            f"{self.topic}/{device_name}",
            json.dumps(state),
            retain=True,
        )

    def publish_all_states(self):
        for dev in DEVICES:
            self.publish_state(dev["friendly_name"])
        log.info("Published state for %d devices", len(DEVICES))

    def run(self):
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        log.info("Connecting to %s:%d...", self.broker, self.port)
        self.client.connect(self.broker, self.port, 60)

        self.publish_bridge_devices()
        self.publish_all_states()

        log.info("Simulator running. Press Ctrl+C to stop.")
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            log.info("Shutting down.")
            self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Simulate Control4 Z2M devices")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="zigbee2mqtt", help="Z2M base topic")
    args = parser.parse_args()

    sim = C4Simulator(args.broker, args.port, args.topic)
    sim.run()


if __name__ == "__main__":
    main()

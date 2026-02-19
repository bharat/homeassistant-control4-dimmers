#!/usr/bin/env python3
"""
Simulate Control4 Z2M devices for development.

Publishes fake Z2M MQTT messages for 6 Control4 devices (2 of each type)
so the custom integration can be developed and tested without real hardware.

Usage:
    python3 scripts/simulate_devices.py [--broker host] [--port 1883]

Requires: paho-mqtt
    pip install paho-mqtt
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
from typing import Any

try:
    import paho.mqtt.client as mqtt_client
except ImportError:
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("c4-simulator")

# ─── Simulated devices ───

DEVICES = [
    {
        "ieee_address": "0x000fff0000aaa001",
        "friendly_name": "Kitchen",
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
        "friendly_name": "Living Room",
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
        "friendly_name": "Theater",
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
        "friendly_name": "Garage",
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
    return [
        {
            "ieee_address": dev["ieee_address"],
            "friendly_name": dev["friendly_name"],
            "model_id": dev["model_id"],
            "manufacturer": "Control4",
            "type": "EndDevice",
            "network_address": random.randint(1000, 65000),  # noqa: S311
            "supported": True,
            "disabled": False,
            "definition": dev["definition"],
            "endpoints": {
                "1": {
                    "bindings": [],
                    "configured_reportings": [],
                    "clusters": {
                        "input": ["genOnOff", "genLevelCtrl"],
                        "output": [],
                    },
                },
                "196": {
                    "bindings": [],
                    "configured_reportings": [],
                    "clusters": {"input": [], "output": []},
                },
                "197": {
                    "bindings": [],
                    "configured_reportings": [],
                    "clusters": {"input": [], "output": []},
                },
            },
        }
        for dev in devices
    ]


def build_device_state(dev: dict) -> dict:
    """Build a realistic state payload for a device."""
    device_type = dev["type"]
    state = {
        "c4_device_type": device_type,
        "linkquality": random.randint(60, 255),  # noqa: S311
    }

    if device_type in ("dimmer", "keypaddim"):
        brightness = random.choice([0, 64, 128, 200, 254])  # noqa: S311
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

    for btn_id in leds.keys() if device_type == "dimmer" else range(6):
        if device_type == "dimmer":
            behavior = "load_on" if btn_id == 1 else "load_off"
            led_mode = "follow_load"
        elif device_type == "keypaddim" and btn_id == 0:
            behavior = "toggle_load"
            led_mode = "follow_load"
        else:
            behavior = "keypad"
            led_mode = "programmed"
        state[f"button_{btn_id}_behavior"] = behavior
        state[f"button_{btn_id}_led_mode"] = led_mode

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
        """Initialize simulator with MQTT connection parameters."""
        self.broker = broker
        self.port = port
        self.topic = topic
        self.client = mqtt_client.Client(client_id=f"c4-simulator-{os.getpid()}")
        self._connected_once = False
        self.device_states: dict[str, dict] = {}

        for dev in DEVICES:
            self.device_states[dev["friendly_name"]] = build_device_state(dev)

    def on_connect(
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        _rc: Any,
    ) -> None:
        """Handle MQTT broker connection and resubscribe."""
        if not self._connected_once:
            self._connected_once = True
            log.info("Connected to MQTT broker at %s:%d", self.broker, self.port)
        else:
            log.debug("Reconnected to MQTT broker")
        client.subscribe(f"{self.topic}/+/set")

    def on_message(
        self,
        _client: Any,
        _userdata: Any,
        msg: Any,
    ) -> None:
        """Handle incoming MQTT set commands."""
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

        # Handle C4 commands: echo action events mimicking real hardware.
        c4_cmd = payload.get("c4_cmd", "")
        if c4_cmd:
            actions, btn_id = self._parse_c4_cmd(c4_cmd)
            if actions:
                self._apply_load_control(state, btn_id)
                for action in actions:
                    log.info("ACTION %s: %s", device_name, action)
                    state["action"] = action
                    self.publish_state(device_name)
                state["action"] = ""
                self.publish_state(device_name)
                return

        for key, value in payload.items():
            state[key] = value

        self.publish_state(device_name)

    @staticmethod
    def _parse_c4_cmd(cmd: str) -> tuple[list[str] | None, int | None]:
        """
        Convert a c4_cmd string to Z2M-style action strings and button ID.

        Returns a list of actions to fire in sequence:
            c4.dmx.bp 01 -> ["button_1_press", "button_1_release", "button_1_click_1"]
            c4.dmx.br 01 -> ["button_1_release"]
            c4.dmx.cc 01 02 -> ["button_1_click_2"]
        """
        # Button press: fire pressed -> released -> single_tap sequence
        bp_match = re.match(r"c4\.dmx\.bp\s+([0-9a-fA-F]+)", cmd)
        if bp_match:
            btn = int(bp_match.group(1), 16)
            return [
                f"button_{btn}_press",
                f"button_{btn}_release",
                f"button_{btn}_click_1",
            ], btn

        # Button release
        br_match = re.match(r"c4\.dmx\.br\s+([0-9a-fA-F]+)", cmd)
        if br_match:
            btn = int(br_match.group(1), 16)
            return [f"button_{btn}_release"], btn

        # Click count (multi-tap)
        cc_match = re.match(r"c4\.dmx\.cc\s+([0-9a-fA-F]+)\s+(\d+)", cmd)
        if cc_match:
            btn = int(cc_match.group(1), 16)
            count = int(cc_match.group(2))
            return [f"button_{btn}_click_{count}"], btn

        return None, None

    @staticmethod
    def _apply_load_control(state: dict, btn_id: int | None) -> None:
        """
        Update dimmer state/brightness when a load-control button is pressed.

        The button's behavior determines whether it affects the load:
          - toggle_load: toggle ON/OFF
          - load_on:     turn ON
          - load_off:    turn OFF
          - keypad:      no load change (scene trigger only)

        Dimmers default to load_on (Top) / load_off (Bottom) when no
        behavior has been explicitly configured.
        """
        if btn_id is None:
            return
        device_type = state.get("c4_device_type")
        if device_type not in ("dimmer", "keypaddim"):
            return

        behavior = state.get(f"button_{btn_id}_behavior", "")
        if not behavior and device_type == "dimmer":
            behavior = "load_on" if btn_id == 1 else "load_off"

        if behavior == "toggle_load":
            if state.get("state") == "ON":
                state["state"] = "OFF"
                state["brightness"] = 0
            else:
                state["state"] = "ON"
                state["brightness"] = 254
        elif behavior == "load_on":
            state["state"] = "ON"
            state["brightness"] = 254
        elif behavior == "load_off":
            state["state"] = "OFF"
            state["brightness"] = 0

    def publish_bridge_devices(self) -> None:
        """Publish the bridge/devices discovery payload."""
        payload = json.dumps(build_bridge_devices(DEVICES))
        self.client.publish(f"{self.topic}/bridge/devices", payload, retain=True)
        log.info("Published bridge/devices with %d Control4 devices", len(DEVICES))

    def publish_state(self, device_name: str) -> None:
        """Publish state for a single device."""
        state = self.device_states.get(device_name)
        if state is None:
            return
        self.client.publish(
            f"{self.topic}/{device_name}",
            json.dumps(state),
            retain=True,
        )

    def publish_all_states(self) -> None:
        """Publish state for all simulated devices."""
        for dev in DEVICES:
            self.publish_state(dev["friendly_name"])
        log.info("Published state for %d devices", len(DEVICES))

    def run(self) -> None:
        """Connect to the broker and run the event loop forever."""
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


def main() -> None:
    """Run the Control4 device simulator."""
    parser = argparse.ArgumentParser(description="Simulate Control4 Z2M devices")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="zigbee2mqtt", help="Z2M base topic")
    args = parser.parse_args()

    sim = C4Simulator(args.broker, args.port, args.topic)
    sim.run()


if __name__ == "__main__":
    main()

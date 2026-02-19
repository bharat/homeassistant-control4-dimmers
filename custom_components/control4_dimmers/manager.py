"""Device manager for Control4 Dimmers."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components import mqtt

from .const import (
    C4_MANUFACTURER_NAME,
    CONF_MQTT_TOPIC,
    DEFAULT_MQTT_TOPIC,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_KEYPADDIM,
    LOGGER,
    SLOT_COUNT,
)
from .models import DeviceConfig, DeviceState, SlotConfig
from .store import Control4Store  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Z2M model strings that identify C4 devices
C4_MODEL_IDS = {
    "C4-APD120",
    "C4-DIM",
    "C4-KD120",
    "C4-KD277",
    "C4-FPD120",
    "C4-KC120277",
    "LDZ-102",
}


class Control4Manager:
    """Manage Control4 device discovery, state, and configuration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: Control4Store,
    ) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._entry = entry
        self._store = store
        self._devices: dict[str, DeviceState] = {}
        self._subscriptions: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._pending_states: dict[str, dict] = {}  # buffered state payloads
        self._event_callbacks: dict[
            tuple[str, int], Callable[[str], None]
        ] = {}  # (ieee, slot_id) -> callback(event_type)

    @property
    def mqtt_topic(self) -> str:
        """Return the configured MQTT base topic."""
        data = self._entry.options or self._entry.data
        return data.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)

    @property
    def devices(self) -> dict[str, DeviceState]:
        """Return all discovered devices keyed by IEEE address."""
        return self._devices

    @property
    def store(self) -> Control4Store:
        """Return the persistent store."""
        return self._store

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for state changes. Returns unsubscribe function."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        """Notify all registered listeners of a state change."""
        for callback in self._listeners:
            callback()

    def register_event_callback(
        self,
        ieee_address: str,
        slot_id: int,
        callback: Callable[[str], None],
    ) -> Callable[[], None]:
        """
        Register a callback for button events on a specific slot.

        The callback receives the event_type string (e.g. "press",
        "double_press").  Returns an unsubscribe callable.
        """
        key = (ieee_address, slot_id)
        self._event_callbacks[key] = callback
        return lambda: self._event_callbacks.pop(key, None)

    def _dispatch_button_action(self, device: DeviceState, action_str: str) -> None:
        """Parse an action string from Z2M and dispatch to event entities."""
        if not action_str:
            return

        # button_N_press  (from c4.dmx.bp)
        press_match = re.match(r"button_(\d+)_press", action_str)
        if press_match:
            slot_id = int(press_match.group(1))
            self._fire_event_callback(device.ieee_address, slot_id, "pressed")
            return

        # button_N_release  (from c4.dmx.br)
        release_match = re.match(r"button_(\d+)_release", action_str)
        if release_match:
            slot_id = int(release_match.group(1))
            self._fire_event_callback(device.ieee_address, slot_id, "released")
            return

        # button_N_click_C  (from c4.dmx.cc)
        click_match = re.match(r"button_(\d+)_click_(\d+)", action_str)
        if click_match:
            slot_id = int(click_match.group(1))
            count = int(click_match.group(2))
            event_type = _click_count_to_event_type(count)
            self._fire_event_callback(device.ieee_address, slot_id, event_type)
            return

    def _fire_event_callback(self, ieee: str, slot_id: int, event_type: str) -> None:
        """Invoke the registered event callback for a slot, if any."""
        cb = self._event_callbacks.get((ieee, slot_id))
        if cb is not None:
            cb(event_type)
            LOGGER.debug("Button event: %s slot %d -> %s", ieee, slot_id, event_type)

    async def async_start(self) -> None:
        """Start MQTT subscriptions for device discovery and state."""
        topic = self.mqtt_topic

        unsub_bridge = await mqtt.async_subscribe(
            self._hass,
            f"{topic}/bridge/devices",
            self._handle_bridge_devices,
        )
        self._subscriptions.append(unsub_bridge)

        unsub_state = await mqtt.async_subscribe(
            self._hass,
            f"{topic}/+",
            self._handle_device_state,
        )
        self._subscriptions.append(unsub_state)

        LOGGER.debug("Control4 manager started, subscribed to %s", topic)

    async def async_stop(self) -> None:
        """Unsubscribe from MQTT."""
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions.clear()

    async def _handle_bridge_devices(  # noqa: PLR0912
        self, msg: mqtt.ReceiveMessage
    ) -> None:
        """Handle zigbee2mqtt/bridge/devices message to discover C4 devices."""
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return

        if not isinstance(payload, list):
            return

        seen = set()
        for device_info in payload:
            if not isinstance(device_info, dict):
                continue
            if not _is_control4_device(device_info):
                continue

            ieee = device_info.get("ieee_address", "")
            if not ieee:
                continue
            seen.add(ieee)
            friendly_name = device_info.get("friendly_name", ieee)
            model_id = device_info.get("model_id", "")

            if ieee not in self._devices:
                self._devices[ieee] = DeviceState(
                    ieee_address=ieee,
                    friendly_name=friendly_name,
                    model_id=model_id,
                )
                LOGGER.info(
                    "Discovered Control4 device: %s (%s) model=%s",
                    friendly_name,
                    ieee,
                    model_id,
                )
            else:
                dev = self._devices[ieee]
                dev.friendly_name = friendly_name
                dev.model_id = model_id

        removed = set(self._devices.keys()) - seen
        for ieee in removed:
            LOGGER.info("Control4 device removed: %s", ieee)
            del self._devices[ieee]

        # Apply any state payloads that arrived before discovery.
        if self._pending_states:
            applied = []
            for name, payload in self._pending_states.items():
                device = self._find_device_by_name(name)
                if device is not None:
                    device.update_from_mqtt(payload)
                    applied.append(name)
            for name in applied:
                del self._pending_states[name]
            if applied:
                LOGGER.debug(
                    "Applied %d buffered state payloads after discovery",
                    len(applied),
                )

        self._notify_listeners()

    async def _handle_device_state(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle per-device state messages from Z2M."""
        topic = msg.topic
        base = self.mqtt_topic

        if topic.startswith(f"{base}/bridge/"):
            return

        device_name = topic[len(base) + 1 :]
        if not device_name or "/" in device_name:
            return

        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return
        if not isinstance(payload, dict):
            return

        device = self._find_device_by_name(device_name)
        if device is None:
            # Device not yet known (bridge/devices may not have arrived).
            # Buffer the payload so it can be applied after discovery.
            self._pending_states[device_name] = payload
            return

        device.update_from_mqtt(payload)

        # Dispatch button action events (press / click) to event entities.
        action = payload.get("action")
        if action:
            self._dispatch_button_action(device, action)

        self._notify_listeners()

    def _find_device_by_name(self, friendly_name: str) -> DeviceState | None:
        """Find a device by its Z2M friendly name."""
        for device in self._devices.values():
            if device.friendly_name == friendly_name:
                return device
        return None

    def get_device_info(self, ieee_address: str) -> dict[str, Any] | None:
        """Get combined device state + config for the websocket API."""
        state = self._devices.get(ieee_address)
        if state is None:
            return None
        config = self._store.get_device(ieee_address)
        return {
            "ieee_address": ieee_address,
            "friendly_name": state.friendly_name,
            "model_id": state.model_id,
            "device_type": state.device_type,
            "available": state.available,
            "brightness": state.brightness,
            "state": state.state,
            "led_colors": {str(k): v for k, v in state.led_colors.items()},
            "button_configs": {str(k): v for k, v in state.button_configs.items()},
            "config": config.to_dict() if config else None,
        }

    def get_all_devices_info(self) -> list[dict[str, Any]]:
        """Get info for all discovered devices."""
        return [
            info
            for ieee in self._devices
            if (info := self.get_device_info(ieee)) is not None
        ]

    async def async_configure_device(
        self,
        ieee_address: str,
        device_type_override: str | None = None,
        slots: list[dict[str, Any]] | None = None,
    ) -> None:
        """Save device configuration and push LED colors via MQTT."""
        state = self._devices.get(ieee_address)
        if state is None:
            LOGGER.error("Cannot configure unknown device: %s", ieee_address)
            return

        config = self._store.get_device(ieee_address) or DeviceConfig(
            ieee_address=ieee_address,
            friendly_name=state.friendly_name,
            device_type=state.device_type or "",
        )

        if device_type_override is not None:
            config.device_type_override = device_type_override or None

        if slots is not None:
            config.slots = [SlotConfig.from_dict(s) for s in slots]

        config.friendly_name = state.friendly_name
        if state.device_type:
            config.device_type = state.device_type

        await self._store.async_save_device(config)

        if slots is not None:
            await self._push_slot_config(state, config)

        self._notify_listeners()

    async def _push_slot_config(self, state: DeviceState, config: DeviceConfig) -> None:
        """Push slot LED colors to the device via MQTT."""
        topic = f"{self.mqtt_topic}/{state.friendly_name}/set"
        for slot in config.slots:
            on_payload = {
                f"color_button_{slot.slot_id}_on": {
                    "hex": f"#{slot.led_on_color}",
                },
            }
            off_payload = {
                f"color_button_{slot.slot_id}_off": {
                    "hex": f"#{slot.led_off_color}",
                },
            }
            behavior_payload = {
                f"button_{slot.slot_id}_behavior": slot.behavior,
                f"button_{slot.slot_id}_led_mode": slot.led_mode,
            }

            for payload in [on_payload, off_payload, behavior_payload]:
                await mqtt.async_publish(self._hass, topic, json.dumps(payload), qos=1)

    async def async_send_mqtt(self, ieee_address: str, payload: dict[str, Any]) -> None:
        """Send an arbitrary MQTT set command to a device."""
        state = self._devices.get(ieee_address)
        if state is None:
            return
        topic = f"{self.mqtt_topic}/{state.friendly_name}/set"
        await mqtt.async_publish(self._hass, topic, json.dumps(payload), qos=1)

    def get_default_slots(self, device_type: str) -> list[SlotConfig]:
        """Generate default slot configuration for a device type."""
        if device_type == DEVICE_TYPE_DIMMER:
            return [
                SlotConfig(
                    slot_id=1,
                    size=1,
                    name="Top",
                    behavior="load_on",
                    led_mode="follow_load",
                    led_on_color="ffffff",
                    led_off_color="000000",
                ),
                SlotConfig(
                    slot_id=4,
                    size=1,
                    name="Bottom",
                    behavior="load_off",
                    led_mode="follow_load",
                    led_on_color="000000",
                    led_off_color="0000ff",
                ),
            ]
        return [
            SlotConfig(
                slot_id=i,
                size=1,
                name=f"Button {i + 1}",
                behavior="toggle_load"
                if device_type == DEVICE_TYPE_KEYPADDIM and i == 0
                else "keypad",
                led_mode="follow_load"
                if device_type == DEVICE_TYPE_KEYPADDIM and i == 0
                else "programmed",
            )
            for i in range(SLOT_COUNT)
        ]


_CLICK_COUNT_MAP: dict[int, str] = {
    1: "single_tap",
    2: "double_tap",
    3: "triple_tap",
}


def _click_count_to_event_type(count: int) -> str:
    """Map a c4.dmx.cc click count to an event_type string."""
    return _CLICK_COUNT_MAP.get(count, f"click_{count}")


def _is_control4_device(device_info: dict) -> bool:
    """Check if a Z2M device info dict is a Control4 device."""
    definition = device_info.get("definition") or {}
    manufacturer = definition.get("manufacturer", "")
    model = definition.get("model", "")
    if C4_MANUFACTURER_NAME.lower() in manufacturer.lower():
        return True
    if model in C4_MODEL_IDS:
        return True
    model_id = device_info.get("model_id", "")
    return model_id in C4_MODEL_IDS

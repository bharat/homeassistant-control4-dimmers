"""Device manager for Control4 Dimmers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components import mqtt
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import device_registry as dr

from .const import (
    C4_MANUFACTURER_NAME,
    CONF_MQTT_TOPIC,
    DEFAULT_MQTT_TOPIC,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_KEYPADDIM,
    DOMAIN,
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
        self._detect_sent: set[str] = set()  # IEEEs we've already sent c4_detect to
        self._event_callbacks: dict[
            tuple[str, int], Callable[[str], None]
        ] = {}  # (ieee, slot_id) -> callback(event_type)
        self._light_track_unsubs: list[Callable[[], None]] = []  # state listeners
        self._led_cooldowns: dict[
            tuple[str, int], float
        ] = {}  # (ieee, slot) -> timestamp

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

    def notify_listeners(self) -> None:
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

        # Press or scene: button_N_press (c4.dmx.bp), button_N_scene (c4.dmx.sc)
        press_match = re.match(r"button_(\d+)_(press|scene)", action_str)
        if press_match:
            slot_id = int(press_match.group(1))
            is_scene = press_match.group(2) == "scene"
            self.fire_button_event(device.ieee_address, slot_id, "pressed")
            config = self._store.get_device(device.ieee_address)
            slot = self._find_slot(config, slot_id) if config else None
            behavior = slot.behavior if slot else "keypad"
            # Scene on load-control: firmware handled the load, software
            # syncs LED tracking. All three load behaviors now have
            # matching firmware modes (00=on, 01=off, 02=toggle).
            # Press on programmable: execute tap_action immediately
            # (unless double_tap configured — handled by click_count).
            is_load = behavior in ("load_on", "load_off", "toggle_load")
            should_act = (is_scene and is_load) or (
                not is_scene
                and not is_load
                and (not slot or not slot.double_tap_action)
            )
            if should_act:
                self._hass.async_create_task(
                    self.press_button(device.ieee_address, slot_id, "pressed"),
                    f"c4_action_{device.ieee_address}_{slot_id}",
                )
            return

        # button_N_release  (from c4.dmx.br)
        release_match = re.match(r"button_(\d+)_release", action_str)
        if release_match:
            slot_id = int(release_match.group(1))
            self.fire_button_event(device.ieee_address, slot_id, "released")
            return

        # button_N_click_C  (from c4.dmx.cc)
        click_match = re.match(r"button_(\d+)_click_(\d+)", action_str)
        if click_match:
            slot_id = int(click_match.group(1))
            count = int(click_match.group(2))
            event_type = _click_count_to_event_type(count)
            self.fire_button_event(device.ieee_address, slot_id, event_type)
            # Load-control buttons: firmware handles load, skip software actions
            config = self._store.get_device(device.ieee_address)
            slot = self._find_slot(config, slot_id) if config else None
            if slot and slot.behavior in ("load_on", "load_off", "toggle_load"):
                return
            # Programmable buttons: execute action based on click count
            if count == 1:
                # Only fires when double_tap is configured (debounced)
                if slot and slot.double_tap_action:
                    self._hass.async_create_task(
                        self.press_button(device.ieee_address, slot_id, "single_tap"),
                        f"c4_action_{device.ieee_address}_{slot_id}",
                    )
            elif count == 2:  # noqa: PLR2004
                self._hass.async_create_task(
                    self.press_button(device.ieee_address, slot_id, "double_tap"),
                    f"c4_action_{device.ieee_address}_{slot_id}",
                )
            return

    def fire_button_event(self, ieee: str, slot_id: int, event_type: str) -> None:
        """Fire a button event on the event entity for a slot."""
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
        """Unsubscribe from MQTT and state listeners."""
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions.clear()
        for unsub in self._light_track_unsubs:
            unsub()
        self._light_track_unsubs.clear()

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
                if dev.friendly_name != friendly_name:
                    LOGGER.info(
                        "Device renamed: %s -> %s (%s)",
                        dev.friendly_name,
                        friendly_name,
                        ieee,
                    )
                    dev.friendly_name = friendly_name
                    self._update_device_registry_name(ieee, friendly_name)
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
                    self._maybe_auto_detect(device)
                    applied.append(name)
            for name in applied:
                del self._pending_states[name]
            if applied:
                LOGGER.debug(
                    "Applied %d buffered state payloads after discovery",
                    len(applied),
                )

        self.setup_light_tracking()
        self.notify_listeners()

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

        self._maybe_auto_detect(device)

        self.notify_listeners()

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
        faceplate_color: str | None = None,
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

        if faceplate_color is not None:
            config.faceplate_color = faceplate_color

        config.friendly_name = state.friendly_name
        if state.device_type:
            config.device_type = state.device_type

        await self._store.async_save_device(config)

        if slots is not None:
            await self._push_slot_config(state, config)

        self.setup_light_tracking()
        self.notify_listeners()

    # Map our behavior names to c4.dmx.btn firmware values:
    #   00=load_on, 01=load_off, 02=toggle, 03=programmable
    #   (sends events, no load control), 04=momentary hold, 05=disabled
    _BEHAVIOR_TO_FIRMWARE: ClassVar[dict[str, int]] = {
        "keypad": 3,  # programmable: sends events, software handles actions
        "toggle_load": 2,
        "load_on": 0,
        "load_off": 1,
    }

    async def _push_slot_config(self, state: DeviceState, config: DeviceConfig) -> None:
        """Push slot LED colors and button config to the device via MQTT."""
        LOGGER.debug(
            "Pushing config for %d slots to %s",
            len(config.slots),
            state.friendly_name,
        )
        for slot in config.slots:
            wire_id = slot.slot_id - 1
            # Set firmware button behavior via c4.dmx.btn
            fw_behavior = self._BEHAVIOR_TO_FIRMWARE.get(slot.behavior, 3)
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.btn {wire_id:02x} 01 {fw_behavior:02x}"},
            )
            # Store on/off colors in firmware (modes 03/04).
            # These are NOT visible while mode 05 override is active,
            # but are stored for potential future use if we decode
            # the firmware LED mode command.
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.led {wire_id:02x} 03 {slot.led_on_color}"},
            )
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.led {wire_id:02x} 04 {slot.led_off_color}"},
            )
            # Set immediate LED color via override (mode 05)
            # Mode 05 is persistent and takes priority over modes 03/04
            initial_color = self._resolve_initial_led_color(state, slot)
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.led {wire_id:02x} 05 {initial_color}"},
            )
            # Store behavior and LED mode in Z2M state for frontend
            firmware_led_mode = (
                "programmed" if slot.led_mode == "fixed" else slot.led_mode
            )
            await self.async_send_mqtt(
                state.ieee_address,
                {
                    f"button_{slot.slot_id}_behavior": slot.behavior,
                    f"button_{slot.slot_id}_led_mode": firmware_led_mode,
                },
            )

    def _resolve_initial_led_color(self, state: DeviceState, slot: SlotConfig) -> str:
        """Determine the correct LED color to display right now."""
        if slot.led_mode == "fixed":
            return slot.led_off_color
        # For follow_load and programmed modes, check tracked entity state
        track_id = slot.led_track_entity_id
        if track_id:
            resolved = self._resolve_entity_id(state.ieee_address, track_id)
            if resolved:
                entity_state = self._hass.states.get(resolved)
                if entity_state and entity_state.state == "on":
                    return slot.led_on_color
        return slot.led_off_color

    @staticmethod
    def _find_slot(config: DeviceConfig, slot_id: int) -> SlotConfig | None:
        """Find a slot config by slot ID."""
        for slot in config.slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    def _find_light_entity(self, ieee: str) -> str | None:
        """Find the Z2M light entity for a C4 device by friendly_name match."""
        device = self._devices.get(ieee)
        if device is None:
            return None
        for state in self._hass.states.async_all("light"):
            friendly = state.attributes.get("friendly_name", "")
            if friendly == device.friendly_name:
                return state.entity_id
        return None

    def _resolve_entity_id(self, ieee: str, entity_id: str) -> str | None:
        """Resolve __self_load__ to the actual light entity, or return as-is."""
        if entity_id == "__self_load__":
            return self._find_light_entity(ieee)
        return entity_id

    async def press_button(
        self, ieee: str, slot_id: int, event_type: str = "pressed"
    ) -> None:
        """Handle a button press — firmware load control or software action."""
        config = self._store.get_device(ieee)
        slot = self._find_slot(config, slot_id) if config else None
        behavior = slot.behavior if slot else "keypad"

        if behavior in ("load_on", "load_off", "toggle_load"):
            light_entity = self._find_light_entity(ieee)
            if not light_entity:
                LOGGER.error("press_button: no light entity for %s", ieee)
                return
            svc = {
                "load_on": "turn_on",
                "load_off": "turn_off",
                "toggle_load": "toggle",
            }[behavior]
            await self._hass.services.async_call(
                "light", svc, {"entity_id": light_entity}
            )
        else:
            trigger = {
                "pressed": "tap",
                "single_tap": "tap",
                "double_tap": "double_tap",
                "triple_tap": "triple_tap",
                "hold": "hold",
            }.get(event_type, "tap")
            await self.execute_slot_action(ieee, slot_id, trigger)

    async def execute_slot_action(
        self, ieee: str, slot_id: int, trigger: str = "tap"
    ) -> None:
        """
        Execute the HA-native action dict for a slot.

        Action format: { action: "domain.service", target: { entity_id: "..." } }
        where `action` is a standard HA service name (e.g. "light.toggle").
        """
        config = self._store.get_device(ieee)
        if not config:
            return
        slot = self._find_slot(config, slot_id)
        if not slot:
            return

        action_map = {
            "tap": slot.tap_action,
            "double_tap": slot.double_tap_action,
            "hold": slot.hold_action,
        }
        action = action_map.get(trigger)
        if not action:
            return

        service = action.get("action", "")
        if "." not in service:
            LOGGER.error("Invalid action service: %s", service)
            return

        domain, svc_name = service.split(".", 1)
        target = action.get("target", {})
        entity_id = self._resolve_entity_id(ieee, target.get("entity_id", ""))

        if slot.led_track_entity_id:
            await self.async_optimistic_led(ieee, slot_id)

        service_data = dict(action.get("data", {}))
        if entity_id:
            service_data["entity_id"] = entity_id
        await self._hass.services.async_call(domain, svc_name, service_data)

    async def async_optimistic_led(self, ieee: str, slot_id: int) -> None:
        """
        Send an immediate LED color based on the opposite of the target light state.

        Also sets a cooldown so the tracking callback doesn't send a
        redundant LED command while the toggle is in flight.
        """
        config = self._store.get_device(ieee)
        if not config:
            return
        for slot in config.slots:
            if slot.slot_id == slot_id and slot.led_track_entity_id:
                track_entity = self._resolve_entity_id(ieee, slot.led_track_entity_id)
                if not track_entity:
                    return
                target_state = self._hass.states.get(track_entity)
                is_on = target_state and target_state.state == "on"
                color = slot.led_off_color if is_on else slot.led_on_color
                wire_id = slot_id - 1
                await self.async_send_mqtt(
                    ieee,
                    {"c4_cmd": f"c4.dmx.led {wire_id:02x} 05 {color}"},
                )
                self._led_cooldowns[(ieee, slot_id)] = time.monotonic() + 2.0
                return

    def setup_light_tracking(self) -> None:
        """
        Set up state listeners for all control_light buttons.

        Call after config changes or on startup. Tears down existing
        listeners and rebuilds from current stored config.
        """
        # Tear down existing listeners
        for unsub in self._light_track_unsubs:
            unsub()
        self._light_track_unsubs.clear()

        # Build a map: resolved_entity_id -> [(ieee, slot_id, on_color, off_color)]
        tracking: dict[str, list[tuple[str, int, str, str]]] = {}
        for ieee in self._devices:
            config = self._store.get_device(ieee)
            if not config:
                continue
            for slot in config.slots:
                if slot.led_track_entity_id:
                    resolved = self._resolve_entity_id(ieee, slot.led_track_entity_id)
                    if resolved:
                        tracking.setdefault(resolved, []).append(
                            (ieee, slot.slot_id, slot.led_on_color, slot.led_off_color)
                        )

        if not tracking:
            LOGGER.debug("Light tracking: no tracked entities found")
            return

        async def _on_state_changed(event: Any) -> None:
            entity_id = event.data.get("entity_id")
            if entity_id not in tracking:
                return
            LOGGER.debug("LED tracking: state change detected for %s", entity_id)
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            is_on = new_state.state == "on"
            now = time.monotonic()
            for ieee, slot_id, on_color, off_color in tracking[entity_id]:
                # Skip if an optimistic LED update was sent recently
                cooldown = self._led_cooldowns.get((ieee, slot_id), 0)
                if now < cooldown:
                    continue
                wire_id = slot_id - 1
                color = on_color if is_on else off_color
                # Mode 05 = immediate override — forces the LED to display
                # this color now, bypassing the on/off state logic.
                await self.async_send_mqtt(
                    ieee,
                    {"c4_cmd": f"c4.dmx.led {wire_id:02x} 05 {color}"},
                )

        unsub = self._hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_changed)
        self._light_track_unsubs.append(unsub)
        LOGGER.debug(
            "Light tracking: set up for %d target entities: %s",
            len(tracking),
            list(tracking.keys()),
        )

    def _update_device_registry_name(self, ieee: str, new_name: str) -> None:
        """Update the HA device registry when a device is renamed in Z2M."""
        registry = dr.async_get(self._hass)
        device_entry = registry.async_get_device(identifiers={(DOMAIN, ieee)})
        if device_entry and device_entry.name != new_name:
            registry.async_update_device(device_entry.id, name=new_name)

    def _maybe_auto_detect(self, device: DeviceState) -> None:
        """Send c4_detect if this device hasn't been detected yet."""
        if device.device_type is None and device.ieee_address not in self._detect_sent:
            self._detect_sent.add(device.ieee_address)
            self._hass.async_create_task(
                self._async_delayed_detect(device.ieee_address),
                f"c4_detect_{device.ieee_address}",
            )

    async def _async_delayed_detect(self, ieee_address: str) -> None:
        """Send c4_detect after a short delay to let Z2M finish device setup."""
        await asyncio.sleep(3)
        LOGGER.info("Auto-detecting device type for %s", ieee_address)
        await self.async_send_mqtt(ieee_address, {"c4_detect": True})

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
                    slot_id=2,
                    size=1,
                    name="Top",
                    behavior="load_on",
                    led_mode="follow_load",
                    led_on_color="ffffff",
                    led_off_color="000000",
                ),
                SlotConfig(
                    slot_id=5,
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
                name=f"Button {i}",
                behavior="toggle_load"
                if device_type == DEVICE_TYPE_KEYPADDIM and i == 1
                else "keypad",
                led_mode="follow_load"
                if device_type == DEVICE_TYPE_KEYPADDIM and i == 1
                else "fixed",
            )
            for i in range(1, SLOT_COUNT + 1)
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
    c4 = C4_MANUFACTURER_NAME.lower()
    # Z2M uses "vendor" in definition, but check both for safety
    for key in ("vendor", "manufacturer"):
        if c4 in definition.get(key, "").lower():
            return True
    # Top-level "manufacturer" field
    if c4 in device_info.get("manufacturer", "").lower():
        return True
    model = definition.get("model", "")
    if model in C4_MODEL_IDS:
        return True
    model_id = device_info.get("model_id", "")
    return model_id in C4_MODEL_IDS

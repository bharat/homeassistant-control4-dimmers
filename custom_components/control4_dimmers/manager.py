"""Device manager for Control4 Dimmers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components import mqtt
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback as ha_callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .audit import (
    AuditMismatch,
    compare_three_way,
    pick_stalest,
    summarize_layers,
)
from .const import (
    AUDIT_MODE_HEAL,
    AUDIT_MODES,
    BUTTON_EVENT_TYPES,
    C4_MANUFACTURER_NAME,
    CONF_AUDIT_INTERVAL,
    CONF_AUDIT_MODE,
    CONF_MQTT_TOPIC,
    DEFAULT_AUDIT_INTERVAL,
    DEFAULT_AUDIT_MODE,
    DEFAULT_MQTT_TOPIC,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_KEYPADDIM,
    DEVICE_TYPE_SLOTS,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPES,
    DOMAIN,
    LOGGER,
    SLOT_COUNT,
)
from .models import DeviceConfig, DeviceState, SlotConfig, _extract_color
from .store import Control4Store  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Minimum interval (seconds) between consecutive MQTT sends to the same
# device. C4 config pushes are bursts of ~25 raw c4_cmd messages; sent
# back to back they overrun the Zigbee mesh and some are silently dropped
# (issue #144). Pacing sends this far apart makes delivery reliable.
MIN_SEND_INTERVAL = 0.05

# Read-after-write verification (issue #145). After a single-slot push we
# read the slot's written parameters back and compare desired vs observed,
# so a silently dropped mesh write becomes a loud, visible failure instead
# of a wrong-colored LED that nobody notices.
#
# VERIFY_SETTLE_MS is how long we wait after a push before reading. The
# measured firmware settle threshold is under 100ms on a quiescent
# single-device mesh; 250ms is roughly 2.5x margin and is flagged for
# tuning. A read taken before the device has settled returns the PRIOR
# value, so reading too early would look like a drop when it is not.
VERIFY_SETTLE_MS = 250
# Longer settle used for the single retry, giving a congested mesh more
# room before we call the write a hard failure.
VERIFY_RETRY_SETTLE_MS = 750
# One retry only; verification must never turn into a polling loop.
VERIFY_MAX_RETRIES = 1
# The audit sweep's heal gets one extra paced re-push (issue #212). The
# interactive verify path keeps its single retry because the user is
# waiting on it, but the sweep is a background pass: giving up after one
# dropped write parks the slot out of sync until the next cycle (up to a
# full day at MAX_AUDIT_INTERVAL), while a second attempt costs one more
# VERIFY_RETRY_SETTLE_MS wait. Still strictly bounded.
AUDIT_HEAL_MAX_RETRIES = 2
# How long to wait (ms) for the converter's seq-matched read to come back
# before giving up on a verify pass.
VERIFY_TIMEOUT_MS = 5000

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
            tuple[str, int | str], Callable[[str], None]
        ] = {}  # (ieee, slot_id | paddle_id) -> callback(event_type)
        self._light_track_unsubs: list[Callable[[], None]] = []  # state listeners
        self._led_cooldowns: dict[
            tuple[str, int], float
        ] = {}  # (ieee, slot) -> timestamp
        # Per-device send pacing/serialization (issue #144). _send_locks
        # serializes individual publishes to one device and, together with
        # _next_send_at, spaces them >= MIN_SEND_INTERVAL apart. _push_locks
        # serializes whole config pushes so a second push cannot interleave
        # with one already in flight. Both are keyed by IEEE and created
        # lazily, so sends to different devices stay independent.
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._push_locks: dict[str, asyncio.Lock] = {}
        self._next_send_at: dict[str, float] = {}  # ieee -> monotonic deadline
        # Read-after-write verification state (issue #145). _pending_verifies
        # holds one future per (ieee, slot) we are waiting to read back; it is
        # resolved when a device-state payload carries the matching
        # c4_verified_slot marker. _verify_results holds the last verify
        # outcome per (ieee, slot) so the event entity can surface in_sync /
        # observed values. _verifying is the in-flight guard: a (ieee, slot) is
        # present for the entire verify lifecycle (settle delay plus every
        # retry), so a second schedule for the same slot coalesces instead of
        # racing and overwriting the pending future.
        self._pending_verifies: dict[tuple[str, int], asyncio.Future[None]] = {}
        self._verify_results: dict[tuple[str, int], dict[str, Any]] = {}
        self._verifying: set[tuple[str, int]] = set()
        # The exact field values the converter's last read-back carried for
        # a slot, captured in _handle_device_state before the pending future
        # resolves. DeviceState merges reads into whatever it already held,
        # so it cannot tell a freshly read field from a stale cached one;
        # this can.
        self._verify_reads: dict[
            tuple[str, int], tuple[dict[str, str], dict[str, str]]
        ] = {}
        # Periodic audit sweep state (issue #199). _audit_unsub cancels the
        # interval timer. _audit_attempts records when each device was last
        # picked (whether or not its reads succeeded) so an unreachable
        # device goes to the back of the queue instead of being picked
        # every tick. _audit_summaries holds the last pass per device for
        # the coverage sensor. _audit_running keeps ticks from overlapping.
        self._audit_unsub: Callable[[], None] | None = None
        self._audit_attempts: dict[str, datetime] = {}
        self._audit_summaries: dict[str, dict[str, Any]] = {}
        self._audit_running = False

    def _option(self, key: str, default: Any) -> Any:
        """Read a setting from entry options, falling back to entry data."""
        options = self._entry.options or {}
        if key in options:
            return options[key]
        return (self._entry.data or {}).get(key, default)

    @property
    def mqtt_topic(self) -> str:
        """Return the configured MQTT base topic."""
        return self._option(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)

    @property
    def audit_interval(self) -> int:
        """Return the audit sweep interval in minutes (0 disables)."""
        raw = self._option(CONF_AUDIT_INTERVAL, DEFAULT_AUDIT_INTERVAL)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_AUDIT_INTERVAL

    @property
    def audit_mode(self) -> str:
        """Return the audit mode: heal (re-push on mismatch) or report."""
        mode = self._option(CONF_AUDIT_MODE, DEFAULT_AUDIT_MODE)
        return mode if mode in AUDIT_MODES else DEFAULT_AUDIT_MODE

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
        slot_id: int | str,
        callback: Callable[[str], None],
    ) -> Callable[[], None]:
        """
        Register a callback for button events on a specific slot or paddle.

        slot_id is an int (1..6) for a button-array slot, or a paddle id
        string ("paddle_up" / "paddle_down") for a local load paddle half.
        The callback receives the event_type string (e.g. "pressed",
        "double_tap").  Returns an unsubscribe callable.
        """
        key = (ieee_address, slot_id)
        self._event_callbacks[key] = callback
        return lambda: self._event_callbacks.pop(key, None)

    def _dispatch_paddle_action(self, device: DeviceState, action_str: str) -> bool:
        """
        Dispatch a local load paddle action (issue #117).

        paddle_up / paddle_down share the button grammar. Firmware drives the
        load directly, so we only surface the event for automations; there is
        no software action to run. Returns True if the action was a paddle.
        """
        paddle_match = re.match(
            r"(paddle_up|paddle_down)_(?:(press|scene|release)|click_(\d+))$",
            action_str,
        )
        if not paddle_match:
            return False
        paddle_id = paddle_match.group(1)
        verb = paddle_match.group(2)
        if verb == "release":
            event_type = "released"
        elif verb in ("press", "scene"):
            event_type = "pressed"
        else:  # click_N
            event_type = _click_count_to_event_type(int(paddle_match.group(3)))
        self.fire_button_event(device.ieee_address, paddle_id, event_type)
        return True

    def _dispatch_button_action(self, device: DeviceState, action_str: str) -> None:
        """Parse an action string from Z2M and dispatch to event entities."""
        if not action_str:
            return

        if self._dispatch_paddle_action(device, action_str):
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

    def fire_button_event(self, ieee: str, slot_id: int | str, event_type: str) -> None:
        """Fire a button/paddle event on the event entity for a slot."""
        if event_type not in BUTTON_EVENT_TYPES:
            # Defense in depth (issue #178): an undeclared type makes HA's
            # EventEntity raise, aborting the MQTT message handler and
            # dropping the rest of the payload with it.
            LOGGER.debug(
                "Dropping undeclared button event type %s for %s slot %s",
                event_type,
                ieee,
                slot_id,
            )
            return
        cb = self._event_callbacks.get((ieee, slot_id))
        if cb is not None:
            cb(event_type)
            LOGGER.debug("Button event: %s slot %s -> %s", ieee, slot_id, event_type)

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

        self.start_audit_sweep()
        LOGGER.debug("Control4 manager started, subscribed to %s", topic)

    async def async_stop(self) -> None:
        """Unsubscribe from MQTT and state listeners."""
        self.stop_audit_sweep()
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
            self._prune_device_state(ieee)

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

    def _prune_device_state(self, ieee: str) -> None:
        """
        Drop a removed device's per-device bookkeeping.

        Pacing locks and deadlines (issue #144), verify results and
        in-flight guards (issue #145), and audit records (issue #199) are
        all keyed by IEEE and would otherwise grow unbounded as devices
        come and go. Pending verify futures are left in place; the verify
        pass aborts on its own when it wakes to find the device gone (see
        _read_slot), so we never cancel a coroutine that is mid-await.
        """
        self._send_locks.pop(ieee, None)
        self._push_locks.pop(ieee, None)
        self._next_send_at.pop(ieee, None)
        for key in [k for k in self._verify_results if k[0] == ieee]:
            del self._verify_results[key]
        for key in [k for k in self._verifying if k[0] == ieee]:
            self._verifying.discard(key)
        for key in [k for k in self._verify_reads if k[0] == ieee]:
            del self._verify_reads[key]
        self._audit_attempts.pop(ieee, None)
        self._audit_summaries.pop(ieee, None)

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

        # Resolve a pending read-back verify (issue #145). The converter
        # echoes c4_verified_slot alongside the observed values in the same
        # payload, so by the time we get here DeviceState already holds the
        # observed values the verify pass will compare against.
        verified_slot = payload.get("c4_verified_slot")
        if verified_slot is not None:
            self._resolve_verify(device.ieee_address, verified_slot, payload)

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

        # Seed default slots when a device is typed but has no slots yet.
        # Only seed when the caller did not provide slots at all (slots is None),
        # so an explicit empty list is respected as "clear my slots".
        seeded = False
        if slots is None and not config.slots and config.effective_type in DEVICE_TYPES:
            config.slots = self.get_default_slots(config.effective_type)
            seeded = True
            LOGGER.info(
                "Seeded %d default slots for %s (type %s)",
                len(config.slots),
                config.friendly_name,
                config.effective_type,
            )

        await self._store.async_save_device(config)

        if slots is not None or seeded:
            await self._push_slot_config(state, config)

        self.setup_light_tracking()
        self.notify_listeners()

    async def async_push_config(self, ieee_address: str) -> bool:
        """
        Re-push a device's stored config to firmware.

        Resends the stored slot config over MQTT, rebuilds light
        tracking, and notifies listeners without mutating anything in
        the store. Returns False if the device or its config is unknown.
        """
        state = self._devices.get(ieee_address)
        config = self._store.get_device(ieee_address)
        if state is None or config is None:
            LOGGER.error("Cannot push config for unknown device: %s", ieee_address)
            return False
        await self._push_slot_config(state, config)
        self.setup_light_tracking()
        self.notify_listeners()
        return True

    # Map our behavior names to c4.dmx.btn firmware values:
    #   00=load_on, 01=load_off, 02=toggle, 03=programmable
    #   (sends events, no load control), 04=momentary hold, 05=disabled
    _BEHAVIOR_TO_FIRMWARE: ClassVar[dict[str, int]] = {
        "keypad": 3,  # programmable: sends events, software handles actions
        "toggle_load": 2,
        "load_on": 0,
        "load_off": 1,
    }

    # LED-mode selector trio over the wire. Each tuple is
    # (param 00, param 01, param 02-or-None):
    #   param 00 = which wire to listen to for press triggers. For
    #     push_release this must be the slot's own wire; the integration
    #     overrides this dynamically below. For fixed and follow_load,
    #     the value is 0x00 (no press listener).
    #   param 01 = behavior mode (00 = Programmed, 01 = Follow Load,
    #                             02 = Push/Release)
    #   param 02 = connection/load ID, only written for Follow Load
    # Mode 03 (RGB) is reused as "on color" / "load-on color" / "press color"
    # and mode 04 as "off color" / "load-off color" / "release color"
    # depending on the selected mode. Mode 05 is the persistent override
    # used by the integration to drive the LED in software for "fixed".
    _LED_MODE_TO_FIRMWARE: ClassVar[dict[str, tuple[int, int, int | None]]] = {
        "fixed": (0x00, 0x00, None),
        "follow_load": (0x00, 0x01, 0x00),
        # param 00 here is a placeholder; _push_slot_config substitutes
        # the slot's own wire so each push_release LED listens to its
        # own button press, not wire 04's.
        "push_release": (0x00, 0x02, None),
    }

    @staticmethod
    def _device_lock(locks: dict[str, asyncio.Lock], ieee_address: str) -> asyncio.Lock:
        """Return the per-device lock for an IEEE, creating it on first use."""
        lock = locks.get(ieee_address)
        if lock is None:
            lock = asyncio.Lock()
            locks[ieee_address] = lock
        return lock

    async def _push_slot_config(self, state: DeviceState, config: DeviceConfig) -> None:
        """Push every slot's LED colors and button config to the device."""
        LOGGER.debug(
            "Pushing config for %d slots to %s",
            len(config.slots),
            state.friendly_name,
        )
        # Hold the per-device push lock for the whole push so a second push
        # to the same device cannot interleave its commands with this one
        # (issue #144). async_send_mqtt takes a separate, finer send lock,
        # so there is no self-deadlock.
        async with self._device_lock(self._push_locks, state.ieee_address):
            for slot in config.slots:
                await self._send_slot_commands(state, slot)

    async def _push_single_slot(
        self, state: DeviceState, config: DeviceConfig, slot_id: int
    ) -> bool:
        """
        Push only one slot's commands, for a single-slot change (issue #144).

        Avoids re-publishing the whole device when just one slot changed.
        Returns False if the slot is not in the config.
        """
        slot = self._find_slot(config, slot_id)
        if slot is None:
            LOGGER.warning(
                "Cannot push unknown slot %d for %s; nothing sent",
                slot_id,
                state.friendly_name,
            )
            return False
        LOGGER.debug(
            "Pushing config for slot %d to %s",
            slot_id,
            state.friendly_name,
        )
        async with self._device_lock(self._push_locks, state.ieee_address):
            await self._send_slot_commands(state, slot)
        return True

    # Whole-device _push_slot_config / push_config are intentionally NOT
    # auto-verified: verifying every slot would multiply mesh read traffic.
    # Whole-device verification is left as an explicit-only follow-up.

    def schedule_slot_verify(
        self, state: DeviceState, config: DeviceConfig, slot_id: int
    ) -> None:
        """
        Schedule a non-blocking read-back verify for one slot (issue #145).

        Fire-and-forget: the caller's service returns immediately and a
        failed or timed-out verify cannot wedge it. Called after a
        single-slot push has landed.

        At most one verify per (ieee, slot) runs at a time. A second schedule
        while one is in flight coalesces (logs and returns) rather than
        starting a racing pass that could orphan the first's pending future.

        Coalescing is only safe because the slot handed to the in-flight
        verify is a live reference into the store (store.get_device returns
        the stored object; set_slot_led mutates it in place before pushing).
        A write that coalesces is therefore still verified: the pending
        verify compares the device read against the slot's current values
        and its retry re-pushes them on mismatch. If get_device is ever
        changed to return a copy, coalescing would silently skip verifying
        the later write; revisit this guard before making that refactor.
        """
        slot = self._find_slot(config, slot_id)
        if slot is None:
            return
        key = (state.ieee_address, slot_id)
        if key in self._verifying:
            LOGGER.debug(
                "verify already in flight for %s slot %d, coalescing",
                state.friendly_name,
                slot_id,
            )
            return
        # Mark in flight synchronously (before the task actually runs) so a
        # second schedule in the same tick sees the guard. The slot object is
        # passed through so _verify_slot / _verify_once do not look it up again.
        self._verifying.add(key)
        self._hass.async_create_task(
            self._verify_slot(state, config, slot),
            f"c4_verify_{state.ieee_address}_{slot_id}",
        )

    async def _verify_slot(
        self, state: DeviceState, config: DeviceConfig, slot: SlotConfig
    ) -> None:
        """
        Read a slot back after a push and reconcile desired vs observed.

        Waits for the firmware to settle, reads the slot, compares. On a
        mismatch it re-pushes the slot once (bounded by VERIFY_MAX_RETRIES)
        and reads again; a mismatch that survives the retry is logged as an
        ERROR (the loud, visible failure issue #145 wants). A read timeout
        stops the pass without retrying forever.
        """
        slot_id = slot.slot_id
        key = (state.ieee_address, slot_id)
        settle = VERIFY_SETTLE_MS / 1000
        attempt = 0
        try:
            while True:
                await asyncio.sleep(settle)
                result = await self._verify_once(state, slot)
                if result is None:
                    # Read timed out or the device was removed mid-flight;
                    # already logged. Do not retry forever.
                    return
                if result:
                    # Observed matches desired; recorded verified in _verify_once.
                    return
                if attempt >= VERIFY_MAX_RETRIES:
                    LOGGER.error(
                        "Verify failed for %s slot %d after %d retry attempt(s); "
                        "slot left out of sync",
                        state.friendly_name,
                        slot_id,
                        attempt,
                    )
                    return
                attempt += 1
                # Re-push the slot, then read again after a longer settle. The
                # re-push does not schedule its own verify (this loop owns it).
                # The re-lookup inside _push_single_slot is intentional: it is
                # the shared push path and needs its own slot resolution.
                await self._push_single_slot(state, config, slot_id)
                settle = VERIFY_RETRY_SETTLE_MS / 1000
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Verify pass errored for %s slot %d", state.friendly_name, slot_id
            )
        finally:
            # Clear the in-flight guard after the whole lifecycle (settle, every
            # retry, and any exit path: success, mismatch, timeout, abort, or
            # error). This is what guarantees at most one in-flight verify per
            # (ieee, slot), so _pending_verifies[key] can never be overwritten.
            self._verifying.discard(key)

    async def _read_slot(
        self, state: DeviceState, slot_id: int
    ) -> tuple[dict[str, str], dict[str, str]] | None:
        """
        Read one slot back from the device via the converter.

        Publishes a targeted read, awaits the converter's seq-matched
        response (correlated by a future keyed on (ieee, slot)), and
        returns the (led, button) fields that response carried. Returns
        None if the read timed out or the device was removed meanwhile.
        """
        ieee = state.ieee_address
        key = (ieee, slot_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._pending_verifies[key] = future
        self._verify_reads.pop(key, None)
        try:
            await self.async_send_mqtt(ieee, {"c4_verify_slot": slot_id})
            try:
                await asyncio.wait_for(future, timeout=VERIFY_TIMEOUT_MS / 1000)
            except TimeoutError:
                LOGGER.warning(
                    "Verify read timed out for %s slot %d",
                    state.friendly_name,
                    slot_id,
                )
                return None
        finally:
            # With per-slot coalescing there is at most one in-flight verify,
            # so this pop can never drop a different pass's future. A response
            # that arrives in the narrow window between wait_for raising
            # TimeoutError and this pop is harmless: _resolve_verify only acts
            # on a future that is not done, and nothing awaits the result once
            # the timeout has fired.
            self._pending_verifies.pop(key, None)

        device = self._devices.get(ieee)
        if device is None:
            # The device was removed while we awaited the read. Observed values
            # would all be absent, which a compare would read as "in sync"
            # for a device that is gone. Abort; record nothing.
            LOGGER.debug("device %s removed during verify, aborting", ieee)
            self._verify_reads.pop(key, None)
            return None
        read = self._verify_reads.pop(key, None)
        if read is None:
            # Resolved without a payload (should not happen via MQTT); fall
            # back to the merged device state so the pass still completes.
            read = (
                dict(device.led_colors.get(slot_id, {})),
                dict(device.button_configs.get(slot_id, {})),
            )
        return read

    async def _verify_once(self, state: DeviceState, slot: SlotConfig) -> bool | None:
        """
        Run one read-back cycle for a slot.

        Reads the slot, then compares desired vs observed. Returns True on
        a full match, False on a mismatch, or None if the read timed out.
        Records the per-slot verify result whenever a read completes so the
        entity can surface it.
        """
        ieee = state.ieee_address
        slot_id = slot.slot_id
        read = await self._read_slot(state, slot_id)
        if read is None:
            return None
        observed_led, observed_btn = read
        mismatches = self._compare_slot(slot, observed_led, observed_btn)
        for field_name, expected, actual in mismatches:
            LOGGER.warning(
                "Verify mismatch on %s slot %d: %s expected %s, observed %s",
                state.friendly_name,
                slot_id,
                field_name,
                expected,
                actual,
            )
        self._record_verify_result(
            ieee, slot_id, observed_led, observed_btn, in_sync=not mismatches
        )
        return not mismatches

    @staticmethod
    def _compare_slot(
        slot: SlotConfig,
        observed_led: dict[str, str],
        observed_btn: dict[str, str],
    ) -> list[tuple[str, str, str]]:
        """
        Compare a desired slot config against observed device-read values.

        Only fields present in the observed payload are checked; a field the
        firmware could not read is absent and is skipped (unreadable, not a
        mismatch). Colors are compared case-insensitively without a leading
        #. Returns a list of (field, expected, actual) mismatches.
        """
        # "fixed" is the integration's name for the firmware "programmed"
        # LED mode, so map it before comparing against the observed value.
        firmware_mode = "programmed" if slot.led_mode == "fixed" else slot.led_mode
        mismatches: list[tuple[str, str, str]] = []
        if "on" in observed_led and not _color_eq(
            slot.led_on_color, observed_led["on"]
        ):
            mismatches.append(("on_color", slot.led_on_color, observed_led["on"]))
        if "off" in observed_led and not _color_eq(
            slot.led_off_color, observed_led["off"]
        ):
            mismatches.append(("off_color", slot.led_off_color, observed_led["off"]))
        if "led_mode" in observed_btn and observed_btn["led_mode"] != firmware_mode:
            mismatches.append(("led_mode", firmware_mode, observed_btn["led_mode"]))
        if "behavior" in observed_btn and observed_btn["behavior"] != slot.behavior:
            mismatches.append(("behavior", slot.behavior, observed_btn["behavior"]))
        return mismatches

    def _record_verify_result(  # noqa: PLR0913
        self,
        ieee: str,
        slot_id: int,
        observed_led: dict[str, str],
        observed_btn: dict[str, str],
        *,
        in_sync: bool,
        verified_by: str = "write",
        diverged_layer: str | None = None,
    ) -> None:
        """
        Store the outcome of a verify read for one slot (issue #145).

        verified_by is "write" for the read-after-write loop and "audit"
        for the periodic sweep; diverged_layer is the audit's three-way
        verdict (cache / firmware / both) or None when in sync or when the
        pass did not classify.
        """
        now = dt_util.utcnow()
        self._verify_results[(ieee, slot_id)] = {
            "in_sync": in_sync,
            "last_verified": now.isoformat(),
            "last_verified_at": now,
            "verified_by": verified_by,
            "diverged_layer": diverged_layer,
            "observed_on_color": observed_led.get("on"),
            "observed_off_color": observed_led.get("off"),
            "observed_led_mode": observed_btn.get("led_mode"),
            "observed_behavior": observed_btn.get("behavior"),
        }

    def get_verify_result(self, ieee: str, slot_id: int) -> dict[str, Any] | None:
        """Return the last verify outcome for a slot, or None if never run."""
        return self._verify_results.get((ieee, slot_id))

    def _resolve_verify(
        self, ieee: str, slot_id: Any, payload: dict[str, Any] | None = None
    ) -> None:
        """
        Resolve the pending verify future for a slot, if any (issue #145).

        The exact fields the read-back payload carried are stashed first so
        the waiting pass compares against what was actually read, not
        against DeviceState's merge of this read over older cached values.
        """
        try:
            key = (ieee, int(slot_id))
        except (TypeError, ValueError):
            return
        if payload is not None:
            self._verify_reads[key] = _extract_slot_fields(payload, key[1])
        future = self._pending_verifies.get(key)
        if future is not None and not future.done():
            future.set_result(None)

    # ── Periodic config audit sweep (issue #199) ─────────────────────
    #
    # Read-after-write verification only covers slots something writes to.
    # A device nothing touches is never audited, so a drift there stays
    # invisible until a button misbehaves. The sweep closes that gap with a
    # rolling schedule: every audit_interval minutes it picks the single
    # stalest device and verifies every one of its stored slots, so the
    # worst-case staleness is bounded (interval x device count) without a
    # mesh-wide burst. Reads go through async_send_mqtt (paced, issue
    # #144) and take the per-device push lock so they cannot interleave
    # with an in-flight config push.

    def start_audit_sweep(self) -> None:
        """Arm the interval timer that drives the audit sweep."""
        self.stop_audit_sweep()
        interval = self.audit_interval
        if interval <= 0:
            LOGGER.info("Config audit sweep disabled (interval 0)")
            return
        self._audit_unsub = async_track_time_interval(
            self._hass, self._audit_tick, timedelta(minutes=interval)
        )
        LOGGER.info(
            "Config audit sweep armed: one device every %d min, mode=%s",
            interval,
            self.audit_mode,
        )

    def stop_audit_sweep(self) -> None:
        """Cancel the audit interval timer, if armed."""
        if self._audit_unsub is not None:
            self._audit_unsub()
            self._audit_unsub = None

    @ha_callback
    def _audit_tick(self, _now: datetime) -> None:
        """Interval callback: audit the stalest device in a background task."""
        if self._audit_running:
            LOGGER.debug("audit tick skipped: previous pass still running")
            return
        self._hass.async_create_task(self.async_audit_next(), "c4_audit_sweep")

    def _device_verified_at(self, ieee: str, config: DeviceConfig) -> datetime | None:
        """
        Effective verification time of a device for sweep ordering.

        The oldest last_verified across the device's stored slots (None if
        any slot has never been verified), floored at the device's last
        audit attempt so a device whose reads keep timing out still yields
        its turn to the others.
        """
        oldest: datetime | None = None
        for slot in config.slots:
            result = self._verify_results.get((ieee, slot.slot_id))
            when = result.get("last_verified_at") if result else None
            if when is None:
                oldest = None
                break
            if oldest is None or when < oldest:
                oldest = when
        attempted = self._audit_attempts.get(ieee)
        if attempted is None:
            return oldest
        if oldest is None:
            return attempted
        return max(oldest, attempted)

    def _audit_candidates(self) -> dict[str, datetime | None]:
        """Discovered devices with stored slots, keyed to their staleness."""
        candidates: dict[str, datetime | None] = {}
        for ieee in self._devices:
            config = self._store.get_device(ieee)
            if config is None or not config.slots:
                continue
            lock = self._push_locks.get(ieee)
            if lock is not None and lock.locked():
                # A config push is in flight; its own verify covers it and
                # auditing now would queue behind it. Pick another device.
                continue
            candidates[ieee] = self._device_verified_at(ieee, config)
        return candidates

    def pick_audit_target(self) -> str | None:
        """Return the IEEE of the device the sweep should audit next."""
        return pick_stalest(self._audit_candidates())

    async def async_audit_next(self) -> dict[str, Any] | None:
        """Audit the stalest device. Returns its summary, or None if idle."""
        if self._audit_running:
            return None
        ieee = self.pick_audit_target()
        if ieee is None:
            LOGGER.debug("audit sweep: no device with stored config to audit")
            return None
        return await self.async_audit_device(ieee)

    async def async_audit_device(
        self, ieee: str, mode: str | None = None
    ) -> dict[str, Any] | None:
        """
        Verify every stored slot of one device against cache and firmware.

        mode overrides the configured audit mode for this pass ("heal" or
        "report"). Returns a summary dict (also kept for the coverage
        sensor), or None if the device or its config is unknown.
        """
        state = self._devices.get(ieee)
        config = self._store.get_device(ieee)
        if state is None or config is None:
            LOGGER.error("Cannot audit unknown device: %s", ieee)
            return None
        mode = mode if mode in AUDIT_MODES else self.audit_mode
        self._audit_running = True
        started = dt_util.utcnow()
        self._audit_attempts[ieee] = started
        summary: dict[str, Any] = {
            "ieee_address": ieee,
            "friendly_name": state.friendly_name,
            "mode": mode,
            "started": started.isoformat(),
            "slots_checked": 0,
            "slots_skipped": 0,
            "slots_unread": 0,
            "slots_mismatched": 0,
            "slots_healed": 0,
            "slots_unhealed": 0,
            "mismatches": [],
        }
        LOGGER.info(
            "Audit start device=%s ieee=%s slots=%d mode=%s",
            state.friendly_name,
            ieee,
            len(config.slots),
            mode,
        )
        try:
            for slot in list(config.slots):
                if ieee not in self._devices:
                    break
                key = (ieee, slot.slot_id)
                if key in self._verifying:
                    # A write-triggered verify owns this slot right now.
                    summary["slots_skipped"] += 1
                    continue
                self._verifying.add(key)
                try:
                    await self._audit_slot(state, config, slot, mode, summary)
                finally:
                    self._verifying.discard(key)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Audit pass errored for %s", state.friendly_name)
        finally:
            self._audit_running = False
        summary["finished"] = dt_util.utcnow().isoformat()
        self._audit_summaries[ieee] = summary
        LOGGER.info(
            "Audit done device=%s checked=%d mismatched=%d healed=%d "
            "unhealed=%d unread=%d skipped=%d",
            state.friendly_name,
            summary["slots_checked"],
            summary["slots_mismatched"],
            summary["slots_healed"],
            summary["slots_unhealed"],
            summary["slots_unread"],
            summary["slots_skipped"],
        )
        self.notify_listeners()
        return summary

    async def _audit_slot(
        self,
        state: DeviceState,
        config: DeviceConfig,
        slot: SlotConfig,
        mode: str,
        summary: dict[str, Any],
    ) -> None:
        """
        Audit one slot: snapshot the cache, read firmware, compare, act.

        Order matters. The cache snapshot is taken BEFORE the read because
        the converter publishes the read-back values under the same
        button_N_* / c4_led_N_* keys, which refreshes both Z2M's cache and
        our mirror of it. On a mismatch the full evidence is logged before
        anything is re-pushed, so a heal never destroys the evidence of
        what drifted (the gap the Entry keypad incident left us with).
        """
        ieee = state.ieee_address
        slot_id = slot.slot_id
        device = self._devices.get(ieee)
        if device is None:
            return
        cached_led = dict(device.led_colors.get(slot_id, {}))
        cached_btn = dict(device.button_configs.get(slot_id, {}))

        # Hold the push lock across the read so a config push to this
        # device cannot interleave with it (issue #144 serialization). It
        # is released before any heal, which takes the lock itself.
        async with self._device_lock(self._push_locks, ieee):
            read = await self._read_slot(state, slot_id)
        if read is None:
            summary["slots_unread"] += 1
            return
        fw_led, fw_btn = read
        summary["slots_checked"] += 1

        mismatches = compare_three_way(slot, cached_led, cached_btn, fw_led, fw_btn)
        layer = summarize_layers(mismatches)
        if not mismatches:
            self._record_verify_result(
                ieee, slot_id, fw_led, fw_btn, in_sync=True, verified_by="audit"
            )
            LOGGER.debug("Audit ok device=%s slot=%d", state.friendly_name, slot_id)
            return

        # Evidence first: every field, every layer, before any heal.
        summary["slots_mismatched"] += 1
        self._log_audit_mismatch(state, slot_id, mode, mismatches, layer)
        summary["mismatches"].extend(
            {
                "slot_id": slot_id,
                "layer": m.layer,
                "field": m.field,
                "store": m.store,
                "cache": m.cache,
                "firmware": m.firmware,
            }
            for m in mismatches
        )
        self._record_verify_result(
            ieee,
            slot_id,
            fw_led,
            fw_btn,
            in_sync=False,
            verified_by="audit",
            diverged_layer=layer,
        )
        if mode != AUDIT_MODE_HEAL:
            return

        # Heal like the read-after-write loop (re-push, longer settle,
        # read again), but with one extra paced attempt so a single
        # dropped write does not wait a whole audit cycle for its retry.
        healed = False
        for _attempt in range(AUDIT_HEAL_MAX_RETRIES):
            await self._push_single_slot(state, config, slot_id)
            await asyncio.sleep(VERIFY_RETRY_SETTLE_MS / 1000)
            async with self._device_lock(self._push_locks, ieee):
                reread = await self._read_slot(state, slot_id)
            if reread is None:
                break
            fw_led, fw_btn = reread
            remaining = self._compare_slot(slot, fw_led, fw_btn)
            healed = not remaining
            self._record_verify_result(
                ieee,
                slot_id,
                fw_led,
                fw_btn,
                in_sync=healed,
                verified_by="audit",
                diverged_layer=None if healed else layer,
            )
            if healed:
                break
        if healed:
            summary["slots_healed"] += 1
            LOGGER.warning(
                "Audit healed device=%s ieee=%s slot=%d layer=%s",
                state.friendly_name,
                ieee,
                slot_id,
                layer,
            )
        else:
            summary["slots_unhealed"] += 1
            LOGGER.error(
                "Audit heal failed device=%s ieee=%s slot=%d layer=%s after %d "
                "re-push attempt(s); slot left out of sync",
                state.friendly_name,
                ieee,
                slot_id,
                layer,
                AUDIT_HEAL_MAX_RETRIES,
            )

    @staticmethod
    def _log_audit_mismatch(
        state: DeviceState,
        slot_id: int,
        mode: str,
        mismatches: list[AuditMismatch],
        layer: str | None,
    ) -> None:
        """Log one greppable WARNING line per drifted field, plus a verdict."""
        for m in mismatches:
            LOGGER.warning(
                "Audit mismatch device=%s ieee=%s slot=%d field=%s store=%s "
                "cache=%s firmware=%s layer=%s mode=%s",
                state.friendly_name,
                state.ieee_address,
                slot_id,
                m.field,
                m.store,
                m.cache if m.cache is not None else "absent",
                m.firmware if m.firmware is not None else "absent",
                m.layer,
                mode,
            )
        LOGGER.warning(
            "Audit verdict device=%s ieee=%s slot=%d layer=%s fields=%s mode=%s",
            state.friendly_name,
            state.ieee_address,
            slot_id,
            layer,
            ",".join(m.field for m in mismatches),
            mode,
        )

    def get_audit_status(self) -> dict[str, Any]:
        """
        Coverage snapshot for the diagnostic sensor.

        Counts devices with stored config whose slots have all been
        verified at least once (by either loop) versus never, and reports
        the age of the stalest fully verified device. Verification state
        lives in memory, so after a Home Assistant restart every device
        starts as never verified until the sweep reaches it.
        """
        now = dt_util.utcnow()
        total = 0
        never: list[str] = []
        out_of_sync: list[str] = []
        stalest_name: str | None = None
        stalest_at: datetime | None = None
        for ieee, state in self._devices.items():
            config = self._store.get_device(ieee)
            if config is None or not config.slots:
                continue
            total += 1
            oldest: datetime | None = None
            fully = True
            for slot in config.slots:
                result = self._verify_results.get((ieee, slot.slot_id))
                if result is None:
                    fully = False
                    continue
                if result.get("in_sync") is False:
                    out_of_sync.append(f"{state.friendly_name} slot {slot.slot_id}")
                when = result.get("last_verified_at")
                if when is not None and (oldest is None or when < oldest):
                    oldest = when
            if not fully:
                never.append(state.friendly_name)
                continue
            if oldest is not None and (stalest_at is None or oldest < stalest_at):
                stalest_at = oldest
                stalest_name = state.friendly_name
        last = max(
            self._audit_summaries.values(),
            key=lambda s: s.get("finished") or "",
            default=None,
        )
        return {
            "devices_total": total,
            "devices_verified": total - len(never),
            "devices_never_verified": len(never),
            "never_verified": sorted(never),
            "out_of_sync": sorted(out_of_sync),
            "stalest_device": stalest_name,
            "stalest_last_verified": stalest_at.isoformat() if stalest_at else None,
            "stalest_age_minutes": (
                round((now - stalest_at).total_seconds() / 60) if stalest_at else None
            ),
            "interval_minutes": self.audit_interval,
            "mode": self.audit_mode,
            "last_audit_device": last["friendly_name"] if last else None,
            "last_audit_finished": last.get("finished") if last else None,
            "next_audit_device": (
                self._devices[n].friendly_name
                if (n := self.pick_audit_target()) in self._devices
                else None
            ),
        }

    async def _send_slot_commands(self, state: DeviceState, slot: SlotConfig) -> None:
        """Emit the firmware command sequence for a single slot."""
        wire_id = slot.slot_id - 1
        # Set firmware button behavior via c4.dmx.btn.
        fw_behavior = self._BEHAVIOR_TO_FIRMWARE.get(slot.behavior, 3)
        await self.async_send_mqtt(
            state.ieee_address,
            {"c4_cmd": f"c4.dmx.btn {wire_id:02x} 01 {fw_behavior:02x}"},
        )
        # Select the LED behavior mode via the param 00 / 01 / 02
        # selector trio. Param 00 is the wire whose press triggers
        # the behavior; for push_release this must be the slot's
        # own wire (otherwise the LED ends up reacting to a
        # different button's press, which is what produced the
        # original cross-flash bug). Param 02 is only written for
        # follow_load (the load/connection ID slot).
        param_00, param_01, param_02 = self._LED_MODE_TO_FIRMWARE.get(
            slot.led_mode, (0x00, 0x00, None)
        )
        if slot.led_mode == "push_release":
            param_00 = wire_id
        await self.async_send_mqtt(
            state.ieee_address,
            {"c4_cmd": f"c4.dmx.led {wire_id:02x} 00 {param_00:02x}"},
        )
        await self.async_send_mqtt(
            state.ieee_address,
            {"c4_cmd": f"c4.dmx.led {wire_id:02x} 01 {param_01:02x}"},
        )
        if param_02 is not None:
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.led {wire_id:02x} 02 {param_02:02x}"},
            )
        # Mode-03 / mode-04 RGB color slots. Active LED behavior
        # decides their meaning:
        #   - follow_load: load-on color / load-off color
        #   - push_release: press color / release color
        #   - fixed (Programmed): mode 03 / mode 04 alone don't light
        #     the LED; the mode-05 override below is what drives it.
        await self.async_send_mqtt(
            state.ieee_address,
            {"c4_cmd": f"c4.dmx.led {wire_id:02x} 03 {slot.led_on_color}"},
        )
        await self.async_send_mqtt(
            state.ieee_address,
            {"c4_cmd": f"c4.dmx.led {wire_id:02x} 04 {slot.led_off_color}"},
        )
        # Programmed-mode display. Without a Composer programming
        # engine driving the firmware's internal "state", neither
        # mode 03 nor mode 04 lights the LED in Programmed mode.
        # Mode 05 is the explicit override that does. The chassis
        # editor's single "Color" picker for fixed mode binds to
        # led_off_color, so the user's picked color lives there.
        if slot.led_mode == "fixed":
            await self.async_send_mqtt(
                state.ieee_address,
                {"c4_cmd": f"c4.dmx.led {wire_id:02x} 05 {slot.led_off_color}"},
            )
        # Store behavior and LED mode in Z2M state for frontend.
        firmware_led_mode = "programmed" if slot.led_mode == "fixed" else slot.led_mode
        await self.async_send_mqtt(
            state.ieee_address,
            {
                f"button_{slot.slot_id}_behavior": slot.behavior,
                f"button_{slot.slot_id}_led_mode": firmware_led_mode,
            },
        )

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
            if light_entity:
                svc = {
                    "load_on": "turn_on",
                    "load_off": "turn_off",
                    "toggle_load": "toggle",
                }[behavior]
                await self._hass.services.async_call(
                    "light", svc, {"entity_id": light_entity}
                )
            else:
                # No HA light entity matches this device's friendly
                # name. That's the normal case when the keypad controls
                # its own local load directly in firmware: the physical
                # button never went through HA, so no light entity got
                # auto-discovered with a matching name. Fake a press by
                # publishing the on/off command straight to the
                # device's Z2M topic; Z2M sends the standard Zigbee
                # on/off cluster command and the keypad firmware
                # toggles the local load just as it would for a
                # physical button press.
                state_value = {
                    "load_on": "ON",
                    "load_off": "OFF",
                    "toggle_load": "TOGGLE",
                }[behavior]
                await self.async_send_mqtt(ieee, {"state": state_value})
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
        """
        Send an MQTT set command to a device, paced and serialized per device.

        Sends to the same device are serialized and spaced at least
        MIN_SEND_INTERVAL apart, so a burst of config commands does not
        overrun the Zigbee mesh and get silently dropped (issue #144).
        Ordering is FIFO per device, which keeps c4_cmd commands and the
        button_N_* state writes that describe them in the order they were
        issued. Sends to different devices use different locks and may
        overlap. A publish that raises propagates to the caller unchanged.
        """
        state = self._devices.get(ieee_address)
        if state is None:
            return
        topic = f"{self.mqtt_topic}/{state.friendly_name}/set"
        async with self._device_lock(self._send_locks, ieee_address):
            wait = self._next_send_at.get(ieee_address, 0.0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if "c4_cmd" in payload:
                LOGGER.info("MQTT -> %s: %s", state.friendly_name, payload["c4_cmd"])
            try:
                await mqtt.async_publish(self._hass, topic, json.dumps(payload), qos=1)
            finally:
                # Schedule the next allowed send even if this one raised, so
                # a transient publish error does not let a later retry burst.
                self._next_send_at[ieee_address] = time.monotonic() + MIN_SEND_INTERVAL

    def get_default_slots(self, device_type: str) -> list[SlotConfig]:
        """Generate default slot configuration for a device type."""
        if device_type in (DEVICE_TYPE_DIMMER, DEVICE_TYPE_SWITCH):
            # Two-button rocker: top turns the load on, bottom turns it
            # off. The relay-only switch shares the layout but reports its
            # bottom half on a different wire (DEVICE_TYPE_SLOTS).
            top, bottom = DEVICE_TYPE_SLOTS[device_type]
            return [
                SlotConfig(
                    slot_id=top,
                    size=1,
                    name="Top",
                    behavior="load_on",
                    led_mode="follow_load",
                    led_on_color="ffffff",
                    led_off_color="000000",
                ),
                SlotConfig(
                    slot_id=bottom,
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
    """
    Map a c4.dmx.cc click count to a declared event type.

    The converter passes the firmware's click counter through unclamped, so
    four or more rapid taps arrive as click_4, click_5, and so on. HA's
    event entity raises on any type outside BUTTON_EVENT_TYPES, aborting
    the whole MQTT message handler (issue #178), so clamp to the declared
    range instead.
    """
    return _CLICK_COUNT_MAP[min(max(count, 1), max(_CLICK_COUNT_MAP))]


def _color_eq(a: str, b: str) -> bool:
    """Compare two hex colors case-insensitively, ignoring a leading #."""
    return a.lstrip("#").lower() == b.lstrip("#").lower()


def _extract_slot_fields(
    payload: dict[str, Any], slot_id: int
) -> tuple[dict[str, str], dict[str, str]]:
    """Pull one slot's LED and button fields out of a Z2M state payload."""
    led: dict[str, str] = {}
    btn: dict[str, str] = {}
    if f"c4_led_{slot_id}_on" in payload:
        led["on"] = _extract_color(payload[f"c4_led_{slot_id}_on"])
    if f"c4_led_{slot_id}_off" in payload:
        led["off"] = _extract_color(payload[f"c4_led_{slot_id}_off"])
    if f"button_{slot_id}_behavior" in payload:
        btn["behavior"] = payload[f"button_{slot_id}_behavior"]
    if f"button_{slot_id}_led_mode" in payload:
        btn["led_mode"] = payload[f"button_{slot_id}_led_mode"]
    return led, btn


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

"""Custom integration for Control4 Dimmers with Home Assistant."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DEVICE_TYPE_SLOTS,
    DEVICE_TYPES,
    DOMAIN,
    INTEGRATION_VERSION,
    LOGGER,
)
from .frontend import JSModuleRegistration
from .manager import Control4Manager
from .store import Control4Store

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [
    Platform.EVENT,
    Platform.SENSOR,
]

# The "control this device's own load" sentinel an action target can
# carry. Stored verbatim; the manager resolves it at execution time.
SELF_LOAD_SENTINEL = "__self_load__"

# Firmware-supported button behaviors (manager._BEHAVIOR_TO_FIRMWARE).
_BEHAVIORS = ["keypad", "load_on", "load_off", "toggle_load"]

# LED behavior modes (manager._LED_MODE_TO_FIRMWARE).
_LED_MODES = ["fixed", "follow_load", "push_release"]

_HEX_RE = re.compile(r"[0-9a-fA-F]{6}")


def _hex_color(value: Any) -> str:
    """Validate a 6-digit hex color, accepting and stripping a leading #."""
    if not isinstance(value, str):
        msg = "color must be a string"
        raise vol.Invalid(msg)
    stripped = value.lstrip("#")
    if not _HEX_RE.fullmatch(stripped):
        msg = f"color must be 6-digit hex (e.g. ff0000), got {value!r}"
        raise vol.Invalid(msg)
    return stripped.lower()


def _action_field(value: Any) -> Any:
    """Validate an action field: the self-load sentinel or an action dict."""
    if value == SELF_LOAD_SENTINEL:
        return value
    if not isinstance(value, dict):
        msg = "action must be a mapping or the '__self_load__' sentinel"
        raise vol.Invalid(msg)
    if "action" not in value and "service" not in value:
        msg = "action mapping must include an 'action' or 'service' key"
        raise vol.Invalid(msg)
    return value


# Strict per-slot validation, shared by set_device_config and set_slot.
# slot_id range is checked separately against the device's effective type.
_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("slot_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("size"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("name"): cv.string,
        vol.Optional("behavior"): vol.In(_BEHAVIORS),
        vol.Optional("led_mode"): vol.In(_LED_MODES),
        vol.Optional("led_on_color"): _hex_color,
        vol.Optional("led_off_color"): _hex_color,
        vol.Optional("tap_action"): vol.Any(None, _action_field),
        vol.Optional("double_tap_action"): vol.Any(None, _action_field),
        vol.Optional("hold_action"): vol.Any(None, _action_field),
        vol.Optional("led_track_entity_id"): vol.Any(None, cv.string),
    }
)


def _validate_slot(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one slot dict, raising ServiceValidationError on bad input."""
    try:
        return _SLOT_SCHEMA(raw)
    except vol.Invalid as err:
        raise ServiceValidationError(str(err)) from err


def _resolve_ieee(
    hass: HomeAssistant,
    manager: Control4Manager,
    data: dict[str, Any],
) -> str:
    """
    Resolve the IEEE address from a service call's device identifiers.

    Accepts exactly one of entity_id (any entity belonging to the
    device) or ieee_address. Raises ServiceValidationError if neither
    or both are given, or if the device cannot be resolved to a known
    discovered device.
    """
    entity_id = data.get("entity_id")
    ieee = data.get("ieee_address")
    if (entity_id is None) == (ieee is None):
        msg = "Provide exactly one of entity_id or ieee_address"
        raise ServiceValidationError(msg)

    if entity_id is not None:
        state = hass.states.get(entity_id)
        if state is None:
            msg = f"Entity not found: {entity_id}"
            raise ServiceValidationError(msg)
        ieee = state.attributes.get("ieee_address")
        if not ieee:
            msg = f"Entity {entity_id} has no ieee_address attribute"
            raise ServiceValidationError(msg)

    if manager.devices.get(ieee) is None:
        msg = f"Unknown or undiscovered device: {ieee}"
        raise ServiceValidationError(msg)
    return ieee


def _effective_type(
    manager: Control4Manager,
    ieee: str,
    override: str | None = None,
) -> str | None:
    """Return the device's effective type, honoring an incoming override."""
    if override:
        return override
    config = manager.store.get_device(ieee)
    if config and config.effective_type:
        return config.effective_type
    state = manager.devices.get(ieee)
    return state.device_type if state else None


def _validate_slot_id(slot_id: int, effective_type: str | None) -> None:
    """Validate slot_id is legal for the device's effective type."""
    allowed = DEVICE_TYPE_SLOTS.get(effective_type or "")
    if allowed is None:
        msg = (
            f"Cannot validate slot {slot_id}: device type "
            f"{effective_type!r} is unknown. Pass device_type_override."
        )
        raise ServiceValidationError(msg)
    if slot_id not in allowed:
        msg = (
            f"Slot {slot_id} is not valid for a {effective_type} device "
            f"(allowed slots: {allowed})"
        )
        raise ServiceValidationError(msg)


async def _register_websocket_handlers(hass: HomeAssistant) -> None:
    """Register websocket commands used by the Lovelace card."""
    for handler in (
        _ws_get_version,
        _ws_get_devices,
        _ws_configure_device,
        _ws_send_mqtt,
        _ws_device_by_entity,
        _ws_event_entities,
    ):
        websocket_api.async_register_command(hass, handler)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/version"})
@websocket_api.async_response
async def _ws_get_version(
    _hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return the integration version."""
    connection.send_result(msg["id"], {"version": INTEGRATION_VERSION})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/devices"})
@websocket_api.async_response
async def _ws_get_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all discovered Control4 devices with state and config."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_result(msg["id"], [])
        return
    connection.send_result(msg["id"], runtime["manager"].get_all_devices_info())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/device_config",
        vol.Required("ieee_address"): cv.string,
        vol.Optional("device_type_override"): vol.Any(cv.string, None),
        vol.Optional("slots"): list,
        vol.Optional("faceplate_color"): vol.Any(cv.string, None),
    }
)
@websocket_api.async_response
async def _ws_configure_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Save device configuration from the Lovelace card."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    manager: Control4Manager = runtime["manager"]
    await manager.async_configure_device(
        ieee_address=msg["ieee_address"],
        device_type_override=msg.get("device_type_override"),
        slots=msg.get("slots"),
        faceplate_color=msg.get("faceplate_color"),
    )
    info = manager.get_device_info(msg["ieee_address"])
    connection.send_result(msg["id"], info)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/send_mqtt",
        vol.Required("ieee_address"): cv.string,
        vol.Required("payload"): dict,
    }
)
@websocket_api.async_response
async def _ws_send_mqtt(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Send an MQTT command to a device from the card."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    manager: Control4Manager = runtime["manager"]
    await manager.async_send_mqtt(msg["ieee_address"], msg["payload"])
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/device_by_entity",
        vol.Required("entity_id"): cv.string,
    }
)
@websocket_api.async_response
async def _ws_device_by_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Resolve a device from one of our entity IDs."""
    entity_id = msg["entity_id"]
    state = hass.states.get(entity_id)
    if state is None:
        connection.send_error(msg["id"], "not_found", "Entity not found")
        return
    ieee = state.attributes.get("ieee_address")
    if not ieee:
        connection.send_error(msg["id"], "not_found", "No ieee_address attribute")
        return
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    manager: Control4Manager = runtime["manager"]
    info = manager.get_device_info(ieee)
    if info is None:
        connection.send_error(msg["id"], "not_found", "Device not discovered")
        return
    connection.send_result(msg["id"], info)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/event_entities",
        vol.Required("ieee_address"): cv.string,
    }
)
@websocket_api.async_response
async def _ws_event_entities(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """
    Return the event entity IDs per slot for a device.

    The frontend uses these entity IDs with HA's search/related API
    to find linked automations.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    ieee = msg["ieee_address"]
    ent_reg = er.async_get(hass)
    prefix = f"{ieee}_event_"

    result: dict[str, str] = {}  # "slot_N" -> entity_id
    domain_data = hass.data.get(DOMAIN, {})
    entry_ids = [
        eid
        for eid, data in domain_data.items()
        if isinstance(data, dict) and "manager" in data
    ]
    for entry_id in entry_ids:
        for entry in er.async_entries_for_config_entry(ent_reg, entry_id):
            if entry.domain == "event" and entry.unique_id.startswith(prefix):
                suffix = entry.unique_id[len(prefix) :]
                if suffix.isdigit():
                    result[f"slot_{suffix}"] = entry.entity_id

    connection.send_result(msg["id"], result)


async def _svc_set_led(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle control4_dimmers.set_led service call."""
    entity_id = call.data["entity_id"]
    mode = call.data["mode"]
    color = call.data["color"].lstrip("#")

    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("Entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    slot_id = state.attributes.get("slot_id")
    if not ieee or slot_id is None:
        LOGGER.error("Entity %s missing ieee_address/slot_id", entity_id)
        return

    runtime = _get_runtime(hass)
    if runtime is None:
        return
    manager: Control4Manager = runtime["manager"]

    device = manager.devices.get(ieee)
    if device is None:
        return

    wire_id = slot_id - 1

    # Update stored config
    config = manager.store.get_device(ieee)
    if config:
        for s in config.slots:
            if s.slot_id == slot_id:
                if mode == "on":
                    s.led_on_color = color
                else:
                    s.led_off_color = color
                break
        await manager.store.async_save()

    # Mode 05 override is the only way to visibly change the LED.
    # Modes 03/04 are silently stored in firmware but hidden by any
    # active mode 05 override (which _push_slot_config always sets).
    await manager.async_send_mqtt(
        ieee, {"c4_cmd": f"c4.dmx.led {wire_id:02x} 05 {color}"}
    )

    manager.notify_listeners()


async def _svc_press_button(hass: HomeAssistant, call: ServiceCall) -> None:
    """
    Handle control4_dimmers.press_button service call.

    For load-control buttons (load_on, load_off, toggle_load), controls
    the dimmer load via the Z2M light entity.  For programmable buttons,
    executes the tap_action.
    """
    entity_id = call.data["entity_id"]
    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("press_button: entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    slot_id = state.attributes.get("slot_id")
    LOGGER.debug(
        "press_button: entity=%s ieee=%s slot=%s",
        entity_id,
        ieee,
        slot_id,
    )
    if not ieee or slot_id is None:
        LOGGER.error("press_button: missing ieee_address/slot_id")
        return

    runtime = _get_runtime(hass)
    if runtime is None:
        LOGGER.error("press_button: runtime not loaded")
        return

    event_type = call.data.get("event_type", "pressed")
    manager: Control4Manager = runtime["manager"]
    manager.fire_button_event(ieee, slot_id, event_type)
    await manager.press_button(ieee, slot_id, event_type)


async def _svc_send_raw_command(hass: HomeAssistant, call: ServiceCall) -> None:
    """
    Handle control4_dimmers.send_raw_command service call.

    Forwards a raw c4_cmd string to the device's MQTT topic so it lands
    on the proprietary text protocol verbatim.  Intended for protocol
    experimentation, not normal use.
    """
    entity_id = call.data["entity_id"]
    command = call.data["command"]
    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("send_raw_command: entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    if not ieee:
        LOGGER.error("send_raw_command: entity %s missing ieee_address", entity_id)
        return

    runtime = _get_runtime(hass)
    if runtime is None:
        return
    manager: Control4Manager = runtime["manager"]
    await manager.async_send_mqtt(ieee, {"c4_cmd": command})


async def _svc_set_slot_led(hass: HomeAssistant, call: ServiceCall) -> None:
    """
    Handle control4_dimmers.set_slot_led service call.

    Updates the LED mode and/or colors for a single slot, then re-
    publishes the slot's full config so the changes land on the
    device. Fields not provided keep their stored values.
    """
    entity_id = call.data["entity_id"]
    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("set_slot_led: entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    slot_id = state.attributes.get("slot_id")
    if not ieee or slot_id is None:
        LOGGER.error("set_slot_led: entity %s missing ieee_address/slot_id", entity_id)
        return

    runtime = _get_runtime(hass)
    if runtime is None:
        return
    manager: Control4Manager = runtime["manager"]

    config = manager.store.get_device(ieee)
    if config is None:
        LOGGER.error("set_slot_led: no stored config for device %s", ieee)
        return
    slot = next((s for s in config.slots if s.slot_id == slot_id), None)
    if slot is None:
        LOGGER.error("set_slot_led: no slot %s on device %s", slot_id, ieee)
        return

    if "led_mode" in call.data:
        slot.led_mode = call.data["led_mode"]
    if "on_color" in call.data:
        slot.led_on_color = call.data["on_color"].lstrip("#")
    if "off_color" in call.data:
        slot.led_off_color = call.data["off_color"].lstrip("#")

    await manager.store.async_save_device(config)

    device_state = manager.devices.get(ieee)
    if device_state is not None:
        await manager._push_slot_config(device_state, config)  # noqa: SLF001
    manager.setup_light_tracking()
    manager.notify_listeners()


async def _svc_set_device_type(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle control4_dimmers.set_device_type service call."""
    entity_id = call.data["entity_id"]
    device_type = call.data["device_type"]
    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("Entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    if not ieee:
        LOGGER.error("Entity %s missing ieee_address", entity_id)
        return

    runtime = _get_runtime(hass)
    if runtime is None:
        return
    manager: Control4Manager = runtime["manager"]
    await manager.async_configure_device(
        ieee_address=ieee,
        device_type_override=device_type,
    )


async def _svc_set_device_config(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    """
    Handle control4_dimmers.set_device_config service call.

    Applies a full device configuration in one call: optional
    device_type_override, optional faceplate_color, and an optional
    slots list that replaces the device's entire slot configuration.
    Omitted fields are left unchanged. Routes through
    async_configure_device so persistence, the firmware push, and entity
    refresh stay identical to the Lovelace card's websocket path.
    Returns the device's resulting stored config.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    device_type_override = call.data.get("device_type_override")
    raw_slots = call.data.get("slots")

    slots: list[dict[str, Any]] | None = None
    if raw_slots is not None:
        effective = _effective_type(manager, ieee, device_type_override)
        slots = []
        for raw in raw_slots:
            validated = _validate_slot(raw)
            _validate_slot_id(validated["slot_id"], effective)
            slots.append(validated)

    await manager.async_configure_device(
        ieee_address=ieee,
        device_type_override=device_type_override,
        slots=slots,
        faceplate_color=call.data.get("faceplate_color"),
    )

    config = manager.store.get_device(ieee)
    return config.to_dict() if config else None


async def _svc_set_slot(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    """
    Handle control4_dimmers.set_slot service call.

    Sets or replaces a single slot's config without resending every
    slot. Omitted slot fields default from the existing slot if one is
    present, otherwise from SlotConfig's dataclass defaults. The
    resulting full slot list is routed through async_configure_device so
    save, push, and refresh happen exactly as for set_device_config.
    Returns the device's resulting stored config.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)

    raw_slot = {
        k: v for k, v in call.data.items() if k not in ("entity_id", "ieee_address")
    }
    validated = _validate_slot(raw_slot)
    slot_id = validated["slot_id"]

    _validate_slot_id(slot_id, _effective_type(manager, ieee))

    config = manager.store.get_device(ieee)
    existing_slots = config.slots if config else []
    existing = next((s for s in existing_slots if s.slot_id == slot_id), None)
    merged = {**(existing.to_dict() if existing else {}), **validated}
    merged["slot_id"] = slot_id

    new_slots: list[dict[str, Any]] = []
    replaced = False
    for slot in existing_slots:
        if slot.slot_id == slot_id:
            new_slots.append(merged)
            replaced = True
        else:
            new_slots.append(slot.to_dict())
    if not replaced:
        new_slots.append(merged)

    await manager.async_configure_device(ieee_address=ieee, slots=new_slots)

    config = manager.store.get_device(ieee)
    return config.to_dict() if config else None


async def _svc_push_config(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """
    Handle control4_dimmers.push_config service call.

    Explicitly re-pushes the device's stored config to firmware and
    re-runs light tracking, so pushing is no longer only an implicit
    side effect of other services. Does not mutate the stored config.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    pushed = await manager.async_push_config(ieee)
    return {"pushed": pushed, "ieee_address": ieee}


async def _svc_snapshot(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """
    Handle control4_dimmers.snapshot service call.

    Saves the device's current stored config under a name so it can be
    restored later. Raises if the device has no stored config yet.
    Returns the ieee, the name, and the captured config dict.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    name = call.data["name"]

    config = manager.store.get_device(ieee)
    if config is None:
        msg = f"No stored config to snapshot for device {ieee}"
        raise ServiceValidationError(msg)

    config_dict = config.to_dict()
    await manager.store.async_save_snapshot(ieee, name, config_dict)
    return {"ieee_address": ieee, "name": name, "snapshot": config_dict}


async def _svc_restore(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """
    Handle control4_dimmers.restore service call.

    Re-applies a named snapshot, persisting and pushing to firmware the
    same way push_config does. With delete=True the snapshot is removed
    after a successful restore. Returns the resulting stored config.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    name = call.data["name"]

    snapshot = manager.store.get_snapshot(ieee, name)
    if snapshot is None:
        msg = f"No snapshot named {name!r} for device {ieee}"
        raise ServiceValidationError(msg)

    await manager.async_configure_device(
        ieee_address=ieee,
        device_type_override=snapshot.get("device_type_override"),
        slots=snapshot.get("slots", []),
        faceplate_color=snapshot.get("faceplate_color"),
    )

    if call.data.get("delete", False):
        await manager.store.async_delete_snapshot(ieee, name)

    config = manager.store.get_device(ieee)
    return {
        "ieee_address": ieee,
        "name": name,
        "restored": config.to_dict() if config else None,
    }


async def _svc_list_snapshots(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """
    Handle control4_dimmers.list_snapshots service call.

    Returns the sorted snapshot names stored for the device.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    return {"ieee_address": ieee, "snapshots": manager.store.list_snapshots(ieee)}


async def _svc_delete_snapshot(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    """
    Handle control4_dimmers.delete_snapshot service call.

    Removes a named snapshot. Returns deleted=False if it did not exist.
    """
    runtime = _get_runtime(hass)
    if runtime is None:
        msg = "Integration not loaded"
        raise ServiceValidationError(msg)
    manager: Control4Manager = runtime["manager"]

    ieee = _resolve_ieee(hass, manager, call.data)
    name = call.data["name"]
    deleted = await manager.store.async_delete_snapshot(ieee, name)
    return {"ieee_address": ieee, "name": name, "deleted": deleted}


async def _register_services(hass: HomeAssistant) -> None:
    """Register custom service calls."""

    async def _wrap_set_led(call: ServiceCall) -> None:
        await _svc_set_led(hass, call)

    async def _wrap_press_button(call: ServiceCall) -> None:
        await _svc_press_button(hass, call)

    async def _wrap_send_raw_command(call: ServiceCall) -> None:
        await _svc_send_raw_command(hass, call)

    async def _wrap_set_slot_led(call: ServiceCall) -> None:
        await _svc_set_slot_led(hass, call)

    async def _wrap_set_device_type(call: ServiceCall) -> None:
        await _svc_set_device_type(hass, call)

    async def _wrap_set_device_config(call: ServiceCall) -> dict[str, Any] | None:
        return await _svc_set_device_config(hass, call)

    async def _wrap_set_slot(call: ServiceCall) -> dict[str, Any] | None:
        return await _svc_set_slot(hass, call)

    async def _wrap_push_config(call: ServiceCall) -> dict[str, Any]:
        return await _svc_push_config(hass, call)

    async def _wrap_snapshot(call: ServiceCall) -> dict[str, Any]:
        return await _svc_snapshot(hass, call)

    async def _wrap_restore(call: ServiceCall) -> dict[str, Any]:
        return await _svc_restore(hass, call)

    async def _wrap_list_snapshots(call: ServiceCall) -> dict[str, Any]:
        return await _svc_list_snapshots(hass, call)

    async def _wrap_delete_snapshot(call: ServiceCall) -> dict[str, Any]:
        return await _svc_delete_snapshot(hass, call)

    hass.services.async_register(
        DOMAIN,
        "set_led",
        _wrap_set_led,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.string,
                vol.Required("mode"): vol.In(["on", "off"]),
                vol.Required("color"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "press_button",
        _wrap_press_button,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.string,
                vol.Optional("event_type", default="pressed"): vol.In(
                    ["pressed", "single_tap", "double_tap", "triple_tap", "hold"]
                ),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "send_raw_command",
        _wrap_send_raw_command,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.string,
                vol.Required("command"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_slot_led",
        _wrap_set_slot_led,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.string,
                vol.Optional("led_mode"): vol.In(
                    ["fixed", "follow_load", "push_release"]
                ),
                vol.Optional("on_color"): cv.string,
                vol.Optional("off_color"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_device_type",
        _wrap_set_device_type,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.string,
                vol.Required("device_type"): vol.In(["dimmer", "keypaddim", "keypad"]),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "set_device_config",
        _wrap_set_device_config,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
                vol.Optional("device_type_override"): vol.Any(
                    None, vol.In(DEVICE_TYPES)
                ),
                vol.Optional("faceplate_color"): vol.Any(None, cv.string),
                vol.Optional("slots"): [dict],
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "set_slot",
        _wrap_set_slot,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
                vol.Required("slot_id"): vol.Coerce(int),
                vol.Optional("size"): vol.Coerce(int),
                vol.Optional("name"): cv.string,
                vol.Optional("behavior"): cv.string,
                vol.Optional("led_mode"): cv.string,
                vol.Optional("led_on_color"): cv.string,
                vol.Optional("led_off_color"): cv.string,
                vol.Optional("tap_action"): vol.Any(None, dict, cv.string),
                vol.Optional("double_tap_action"): vol.Any(None, dict, cv.string),
                vol.Optional("hold_action"): vol.Any(None, dict, cv.string),
                vol.Optional("led_track_entity_id"): vol.Any(None, cv.string),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "push_config",
        _wrap_push_config,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "snapshot",
        _wrap_snapshot,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
                vol.Required("name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "restore",
        _wrap_restore,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
                vol.Required("name"): cv.string,
                vol.Optional("delete", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "list_snapshots",
        _wrap_list_snapshots,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "delete_snapshot",
        _wrap_delete_snapshot,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.string,
                vol.Optional("ieee_address"): cv.string,
                vol.Required("name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _register_frontend(hass: HomeAssistant) -> None:
    """Register frontend resources when HA is ready."""
    if hass.data.get(f"{DOMAIN}_skip_frontend", False):
        return

    async def _register(_: Event | None = None) -> None:
        registration = JSModuleRegistration(hass)
        await registration.async_register()

    if hass.state is CoreState.running:
        await _register()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register)


def _get_runtime(hass: HomeAssistant) -> dict[str, Any] | None:
    """Get the runtime data for the first config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    for data in domain_data.values():
        if isinstance(data, dict) and "manager" in data:
            return data
    return None


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up the Control4 Dimmers integration (once, before entries)."""
    hass.data.setdefault(DOMAIN, {})
    await _register_websocket_handlers(hass)
    await _register_services(hass)
    await _register_frontend(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up a Control4 Dimmers config entry."""
    store = Control4Store(hass, entry.entry_id)
    await store.async_load()

    manager = Control4Manager(hass, entry, store)

    hass.data[DOMAIN][entry.entry_id] = {
        "manager": manager,
        "store": store,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data.get(f"{DOMAIN}_skip_mqtt", False):
        await manager.async_start()

    LOGGER.info("Control4 Dimmers entry set up: %s", entry.title)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = hass.data[DOMAIN].pop(entry.entry_id, None)
        if runtime:
            await runtime["manager"].async_stop()
    return unload_ok


async def async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

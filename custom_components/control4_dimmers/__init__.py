"""Custom integration for Control4 Dimmers with Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, INTEGRATION_VERSION, LOGGER
from .frontend import JSModuleRegistration
from .manager import Control4Manager
from .store import Control4Store

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SENSOR,
]


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
    import json  # noqa: PLC0415

    from homeassistant.components import mqtt  # noqa: PLC0415

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
    mode_code = "03" if mode == "on" else "04"
    topic = f"{manager.mqtt_topic}/{device.friendly_name}/set"
    payload = {"c4_cmd": f"c4.dmx.led {wire_id:02x} {mode_code} {color}"}
    await mqtt.async_publish(hass, topic, json.dumps(payload), qos=1)

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

    manager.notify_listeners()


async def _svc_press_button(hass: HomeAssistant, call: ServiceCall) -> None:
    """
    Handle control4_dimmers.press_button service call.

    For load buttons (load_on, load_off, toggle_load): controls the dimmer
    load via the light entity using standard Zigbee genOnOff/genLevelCtrl.
    Real C4 devices reject simulated button presses (c4.dmx.bp).

    For keypad buttons: fires the event entity so HA automations trigger.
    """
    entity_id = call.data["entity_id"]
    state = hass.states.get(entity_id)
    if state is None:
        LOGGER.error("press_button: entity not found: %s", entity_id)
        return
    ieee = state.attributes.get("ieee_address")
    slot_id = state.attributes.get("slot_id")
    behavior = state.attributes.get("behavior", "keypad")
    LOGGER.info(
        "press_button: entity=%s ieee=%s slot=%s behavior=%s",
        entity_id,
        ieee,
        slot_id,
        behavior,
    )
    if not ieee or slot_id is None:
        LOGGER.error("press_button: missing ieee_address/slot_id")
        return

    if _get_runtime(hass) is None:
        LOGGER.error("press_button: runtime not loaded")
        return

    if behavior in ("load_on", "load_off", "toggle_load"):
        light_entity_id = _find_light_entity(hass, ieee)
        LOGGER.info("press_button: light_entity=%s", light_entity_id)
        if not light_entity_id:
            LOGGER.error("press_button: no light entity for %s", ieee)
            return

        service_data = {"entity_id": light_entity_id}
        if behavior == "load_on":
            LOGGER.info("press_button: calling light.turn_on on %s", light_entity_id)
            await hass.services.async_call("light", "turn_on", service_data)
        elif behavior == "load_off":
            LOGGER.info("press_button: calling light.turn_off on %s", light_entity_id)
            await hass.services.async_call("light", "turn_off", service_data)
        elif behavior == "toggle_load":
            LOGGER.info("press_button: calling light.toggle on %s", light_entity_id)
            await hass.services.async_call("light", "toggle", service_data)
    else:
        # Keypad button — fire the event entity
        bus_data = {
            "entity_id": entity_id,
            "ieee_address": ieee,
            "slot_id": slot_id,
        }
        hass.bus.async_fire(f"{DOMAIN}_button_press", bus_data)
        LOGGER.debug("Fired keypad button event for %s slot %s", ieee, slot_id)


def _find_light_entity(hass: HomeAssistant, ieee: str) -> str | None:
    """Find the dimmer light entity for a device by IEEE address."""
    for state in hass.states.async_all("light"):
        if state.attributes.get("ieee_address") == ieee:
            return state.entity_id
    return None


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


async def _register_services(hass: HomeAssistant) -> None:
    """Register custom service calls."""

    async def _wrap_set_led(call: ServiceCall) -> None:
        await _svc_set_led(hass, call)

    async def _wrap_press_button(call: ServiceCall) -> None:
        await _svc_press_button(hass, call)

    async def _wrap_set_device_type(call: ServiceCall) -> None:
        await _svc_set_device_type(hass, call)

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

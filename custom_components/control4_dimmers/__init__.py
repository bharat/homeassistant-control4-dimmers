"""Custom integration for Control4 Dimmers with Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, INTEGRATION_VERSION, LOGGER
from .frontend import JSModuleRegistration
from .manager import Control4Manager
from .store import Control4Store

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SELECT,
]

type Control4ConfigEntry = ConfigEntry


async def _register_websocket_handlers(hass: HomeAssistant) -> None:
    """Register websocket commands used by the Lovelace card."""

    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/version"})
    @websocket_api.async_response
    async def websocket_get_version(
        _hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict,
    ) -> None:
        connection.send_result(msg["id"], {"version": INTEGRATION_VERSION})

    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/devices"})
    @websocket_api.async_response
    async def websocket_get_devices(
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
    async def websocket_configure_device(
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
    async def websocket_send_mqtt(
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
    async def websocket_device_by_entity(
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

    websocket_api.async_register_command(hass, websocket_get_version)
    websocket_api.async_register_command(hass, websocket_get_devices)
    websocket_api.async_register_command(hass, websocket_configure_device)
    websocket_api.async_register_command(hass, websocket_send_mqtt)
    websocket_api.async_register_command(hass, websocket_device_by_entity)


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
    await _register_frontend(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Control4ConfigEntry,
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
    entry: Control4ConfigEntry,
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
    entry: Control4ConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

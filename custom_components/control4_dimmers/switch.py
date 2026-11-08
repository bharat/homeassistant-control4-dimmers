"""
Switch platform for Control4 Dimmers.

Creates a plain on/off load entity for relay-only devices (the switch
type, e.g. C4-SW120277, issue #189). These devices drive a load but
cannot dim it, so they get a switch entity and never a light entity:
a light would render a brightness slider the hardware ignores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity

from .const import DEVICE_TYPE_SWITCH, DOMAIN, LOGGER

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .manager import Control4Manager


def _effective_type(manager: Control4Manager, ieee: str) -> str | None:
    """Return the stored override if any, else the detected type."""
    config = manager.store.get_device(ieee)
    state = manager.devices.get(ieee)
    if config and config.effective_type:
        return config.effective_type
    return state.device_type if state else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Control4 relay load switch entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known: set[str] = set()

    def _check_new_switches() -> None:
        new: list[Control4LoadSwitch] = []
        for ieee, state in manager.devices.items():
            if ieee in known:
                continue
            if _effective_type(manager, ieee) != DEVICE_TYPE_SWITCH:
                continue
            known.add(ieee)
            new.append(
                Control4LoadSwitch(
                    manager=manager,
                    ieee_address=ieee,
                    friendly_name=state.friendly_name,
                    model_id=state.model_id,
                )
            )
        if new:
            async_add_entities(new)
            LOGGER.debug("Added %d load switch entities", len(new))

    _check_new_switches()
    manager.add_listener(_check_new_switches)


class Control4LoadSwitch(SwitchEntity):
    """A switch entity representing a relay-only load (no brightness)."""

    # Push-based: the manager listener below writes state.
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Load"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
    ) -> None:
        """Initialize the load switch entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }
        self._sync_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose ieee_address so the card and services can resolve the device."""
        return {"ieee_address": self._ieee}

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self._unsub_listener = self._manager.add_listener(self._on_manager_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from manager updates."""
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None

    def _on_manager_update(self) -> None:
        """Refresh state from the device when MQTT data arrives."""
        self._sync_state()
        self.async_write_ha_state()

    def _sync_state(self) -> None:
        """Read the current on/off state from DeviceState."""
        device = self._manager.devices.get(self._ieee)
        if device is None:
            self._attr_available = False
            return
        # A device re-typed away from switch (override or re-detection)
        # keeps its entity but goes unavailable rather than lying about a
        # load it no longer models.
        self._attr_available = (
            device.available
            and _effective_type(self._manager, self._ieee) == DEVICE_TYPE_SWITCH
        )
        self._attr_is_on = device.state == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn on the load. Same state write the lights use, minus brightness."""
        self._attr_is_on = True
        await self._manager.async_send_mqtt(self._ieee, {"state": "ON"})
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn off the load."""
        self._attr_is_on = False
        await self._manager.async_send_mqtt(self._ieee, {"state": "OFF"})
        self.async_write_ha_state()

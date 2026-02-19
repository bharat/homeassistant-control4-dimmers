"""
Light platform for Control4 Dimmers.

Creates a dimmer load entity (brightness-only) for devices with a
physical dimmer (dimmer and keypaddim types).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)

from .const import DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM, DOMAIN, LOGGER

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .manager import Control4Manager

LOAD_DEVICE_TYPES = {DEVICE_TYPE_DIMMER, DEVICE_TYPE_KEYPADDIM}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Control4 dimmer load entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known_dimmers: set[str] = set()

    def _check_new_dimmers() -> None:
        new: list[Control4DimmerLight] = []
        for ieee, state in manager.devices.items():
            if ieee in known_dimmers:
                continue
            config = manager.store.get_device(ieee)
            device_type = config.effective_type if config else state.device_type
            if device_type not in LOAD_DEVICE_TYPES:
                continue
            known_dimmers.add(ieee)
            new.append(
                Control4DimmerLight(
                    manager=manager,
                    ieee_address=ieee,
                    friendly_name=state.friendly_name,
                    model_id=state.model_id,
                )
            )
        if new:
            async_add_entities(new)
            LOGGER.debug("Added %d dimmer light entities", len(new))

    _check_new_dimmers()
    manager.add_listener(_check_new_dimmers)


class Control4DimmerLight(LightEntity):
    """A light entity representing the physical dimmer load."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_name = "Load"

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
    ) -> None:
        """Initialize the dimmer light entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_dimmer"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }
        self._sync_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose ieee_address so the card can resolve the device."""
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
        """Read current state/brightness from DeviceState."""
        device = self._manager.devices.get(self._ieee)
        if device is None:
            return
        self._attr_is_on = device.state == "ON"
        self._attr_brightness = device.brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the dimmer load."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)
        payload: dict[str, Any] = {"state": "ON", "brightness": brightness}
        self._attr_is_on = True
        self._attr_brightness = brightness
        await self._manager.async_send_mqtt(self._ieee, payload)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn off the dimmer load."""
        self._attr_is_on = False
        self._attr_brightness = 0
        await self._manager.async_send_mqtt(
            self._ieee, {"state": "OFF", "brightness": 0}
        )
        self.async_write_ha_state()

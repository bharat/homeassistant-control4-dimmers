"""
Sensor platform for Control4 Dimmers.

Two sensor entity types:
  - Control4DeviceSensor: lightweight device anchor (primary entity)
  - Control4AmbientLightSensor: ambient light level from the device's sensor
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import LIGHT_LUX

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .manager import Control4Manager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Control4 sensor entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known_anchors: set[str] = set()
    known_ambient: set[str] = set()

    def _check_new_devices() -> None:
        new_entities: list[SensorEntity] = []
        for ieee, state in manager.devices.items():
            if ieee not in known_anchors:
                known_anchors.add(ieee)
                new_entities.append(
                    Control4DeviceSensor(
                        manager=manager,
                        ieee_address=ieee,
                        friendly_name=state.friendly_name,
                        model_id=state.model_id,
                    )
                )
            if ieee not in known_ambient:
                known_ambient.add(ieee)
                new_entities.append(
                    Control4AmbientLightSensor(
                        manager=manager,
                        ieee_address=ieee,
                        friendly_name=state.friendly_name,
                        model_id=state.model_id,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)
            LOGGER.debug("Added %d sensor entities", len(new_entities))

    _check_new_devices()
    manager.add_listener(_check_new_devices)


class Control4DeviceSensor(SensorEntity):
    """Sensor entity representing a Control4 device (anchor)."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
    ) -> None:
        """Initialize the sensor anchor entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_sensor"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }

    @property
    def native_value(self) -> str:
        """Return connection state."""
        device = self._manager.devices.get(self._ieee)
        if device is None:
            return "disconnected"
        return "connected" if device.available else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose device-level metadata."""
        device = self._manager.devices.get(self._ieee)
        config = self._manager.store.get_device(self._ieee)
        detected = device.device_type if device else None
        effective = (config.effective_type if config else None) or detected
        return {
            "ieee_address": self._ieee,
            "device_type": effective,
            "detected_type": detected,
            "model_id": device.model_id if device else None,
            "load_state": device.state if device else None,
            "load_brightness": device.brightness if device else None,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self._unsub_listener = self._manager.add_listener(self._on_manager_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from manager updates."""
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None

    def _on_manager_update(self) -> None:
        """Refresh state when the manager detects new data."""
        self.async_write_ha_state()


class Control4AmbientLightSensor(SensorEntity):
    """Sensor entity for the device's ambient light level."""

    _attr_has_entity_name = True
    _attr_name = "Ambient Light"
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
    ) -> None:
        """Initialize the ambient light sensor entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_ambient_light"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }

    @property
    def native_value(self) -> int | None:
        """Return the current ambient light reading."""
        device = self._manager.devices.get(self._ieee)
        if device is None:
            return None
        return device.raw.get("ambient_light")

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self._unsub_listener = self._manager.add_listener(self._on_manager_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from manager updates."""
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None

    def _on_manager_update(self) -> None:
        """Refresh state when new data arrives."""
        self.async_write_ha_state()

"""
Select platform for Control4 Dimmers.

Creates a device-type select entity for each discovered Control4 device.
This entity serves as the anchor for the Lovelace card (card config binds
to its entity_id) and lets the user override the auto-detected type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity

from .const import DEVICE_TYPES, DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .manager import Control4Manager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Control4 device-type select entities."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known: set[str] = set()

    def _check_new_devices() -> None:
        new_entities: list[Control4DeviceTypeSelect] = []
        for ieee in manager.devices:
            if ieee not in known:
                known.add(ieee)
                new_entities.append(
                    Control4DeviceTypeSelect(manager=manager, ieee_address=ieee)
                )
        if new_entities:
            async_add_entities(new_entities)
            LOGGER.debug("Added %d device-type select entities", len(new_entities))

    _check_new_devices()
    manager.add_listener(_check_new_devices)


class Control4DeviceTypeSelect(SelectEntity):
    """Select entity representing a Control4 device's type."""

    _attr_has_entity_name = True
    _attr_options = DEVICE_TYPES
    _attr_translation_key = "device_type"

    def __init__(self, manager: Control4Manager, ieee_address: str) -> None:
        """Initialize the select entity."""
        self._manager = manager
        self._ieee = ieee_address
        state = manager.devices.get(ieee_address)
        friendly = state.friendly_name if state else ieee_address
        self._attr_unique_id = f"{ieee_address}_device_type"
        self._attr_name = f"{friendly} Device Type"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly,
            "manufacturer": "Control4",
            "model": state.model_id if state else None,
        }

    @property
    def current_option(self) -> str | None:
        """Return the current device type."""
        config = self._manager.store.get_device(self._ieee)
        if config and config.device_type_override:
            return config.device_type_override
        state = self._manager.devices.get(self._ieee)
        if state and state.device_type:
            return state.device_type
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose ieee_address and friendly_name for the card."""
        state = self._manager.devices.get(self._ieee)
        return {
            "ieee_address": self._ieee,
            "friendly_name": state.friendly_name if state else None,
            "model_id": state.model_id if state else None,
            "detected_type": state.device_type if state else None,
        }

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a device type."""
        await self._manager.async_configure_device(
            ieee_address=self._ieee,
            device_type_override=option,
        )
        self.async_write_ha_state()

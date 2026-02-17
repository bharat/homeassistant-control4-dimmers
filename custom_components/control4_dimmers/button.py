"""Button platform for Control4 Dimmers.

Creates HA button entities for each configured slot on a Control4 device.
Pressing a button entity sends the corresponding button press event via MQTT.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .manager import Control4Manager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Control4 button entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    store = runtime["store"]

    entities: list[Control4ButtonEntity] = []
    for ieee, config in store.devices.items():
        state = manager.devices.get(ieee)
        if state is None:
            continue
        for slot in config.slots:
            entities.append(
                Control4ButtonEntity(
                    manager=manager,
                    ieee_address=ieee,
                    friendly_name=state.friendly_name,
                    slot_id=slot.slot_id,
                    slot_name=slot.name,
                )
            )

    if entities:
        async_add_entities(entities)
        LOGGER.debug("Added %d button entities", len(entities))


class Control4ButtonEntity(ButtonEntity):
    """A button entity representing a Control4 keypad button slot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        slot_id: int,
        slot_name: str,
    ) -> None:
        """Initialize the button entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._slot_id = slot_id
        self._attr_unique_id = f"{ieee_address}_button_{slot_id}"
        self._attr_name = slot_name or f"Button {slot_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
        }

    async def async_press(self) -> None:
        """Handle a button press by sending the action via MQTT."""
        await self._manager.async_send_mqtt(
            self._ieee,
            {"c4_cmd": f"c4.dmx.bp {self._slot_id:02d}"},
        )

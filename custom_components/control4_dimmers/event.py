"""
Event platform for Control4 Dimmers.

Creates HA event entities for each button slot on a Control4 device.
When the physical button is pressed (single / double / triple / quadruple),
the corresponding event entity fires, which can be used as an automation
trigger.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import BUTTON_EVENT_TYPES, DEVICE_TYPE_SLOTS, DOMAIN, LOGGER

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
    """Set up Control4 button event entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known: set[str] = set()  # tracks "ieee_slot" keys already created

    def _check_new_devices() -> None:
        new_entities: list[Control4ButtonEvent] = []
        for ieee, state in manager.devices.items():
            device_type = state.device_type
            if not device_type:
                continue
            slot_ids = DEVICE_TYPE_SLOTS.get(device_type, [])
            for slot_id in slot_ids:
                key = f"{ieee}_{slot_id}"
                if key in known:
                    continue
                known.add(key)
                new_entities.append(
                    Control4ButtonEvent(
                        manager=manager,
                        ieee_address=ieee,
                        friendly_name=state.friendly_name,
                        model_id=state.model_id,
                        slot_id=slot_id,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)
            LOGGER.debug("Added %d button event entities", len(new_entities))

    _check_new_devices()
    manager.add_listener(_check_new_devices)


class Control4ButtonEvent(EventEntity):
    """Event entity representing a button press on a Control4 device."""

    _attr_has_entity_name = True
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = BUTTON_EVENT_TYPES

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
        slot_id: int,
    ) -> None:
        """Initialize the event entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._slot_id = slot_id
        self._default_name = f"Button {slot_id + 1}"
        self._custom_name: str | None = None
        self._unsub_event: Callable[[], None] | None = None
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_event_{slot_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }
        # Read any saved name immediately so entity registration picks it up.
        self._sync_name_from_config()

    @property
    def name(self) -> str:
        """Return the current name, reflecting any user-configured label."""
        return self._custom_name or self._default_name

    async def async_added_to_hass(self) -> None:
        """Register with the manager for button events and config changes."""
        self._unsub_event = self._manager.register_event_callback(
            self._ieee, self._slot_id, self._on_button_event
        )
        self._unsub_listener = self._manager.add_listener(self._on_manager_update)
        self._sync_name_from_config()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from the manager."""
        if self._unsub_event:
            self._unsub_event()
            self._unsub_event = None
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None

    def _on_manager_update(self) -> None:
        """Re-sync name when device config changes."""
        if self._sync_name_from_config():
            LOGGER.debug(
                "Event entity %s name -> %s",
                self._attr_unique_id,
                self.name,
            )
            self._update_entity_id()
            self.async_write_ha_state()

    def _update_entity_id(self) -> None:
        """Update the entity_id in the HA registry to match the current name."""
        if not self.hass or not self.registry_entry:
            return
        ent_reg = er.async_get(self.hass)
        device_name = self._attr_device_info["name"]
        new_entity_id = f"event.{slugify(f'{device_name} {self.name}')}"
        if new_entity_id != self.entity_id:
            ent_reg.async_update_entity(self.entity_id, new_entity_id=new_entity_id)

    def _sync_name_from_config(self) -> bool:
        """Update entity name from stored slot config. Return True if changed."""
        old_name = self._custom_name
        new_name: str | None = None

        config = self._manager.store.get_device(self._ieee)
        if config:
            for slot_cfg in config.slots:
                if slot_cfg.slot_id == self._slot_id and slot_cfg.name:
                    new_name = slot_cfg.name
                    break

        if new_name is None:
            device = self._manager.devices.get(self._ieee)
            if device and device.device_type:
                for default_slot in self._manager.get_default_slots(device.device_type):
                    if default_slot.slot_id == self._slot_id and default_slot.name:
                        new_name = default_slot.name
                        break

        self._custom_name = new_name
        return self._custom_name != old_name

    def _on_button_event(self, event_type: str) -> None:
        """Handle a button event dispatched by the manager."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

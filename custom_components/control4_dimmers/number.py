"""
Number platform for Control4 Dimmers.

Exposes the dimming table (c4.dm.tv): ramp rates, level limits and cold start
values. An entity appears once the converter has read its parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

from .const import DEVICE_TYPE_SWITCH, DOMAIN, LOGGER
from .switch import _effective_type

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .manager import Control4Manager

MAX_RAMP_MS = 60000
# A relay can only switch its load, so it has nothing to ramp.
RAMP_PARAMS = frozenset({"02", "03", "04", "05"})
RELAY_MODELS = frozenset({"C4-SW120277"})


def _level(param: str, name: str) -> NumberEntityDescription:
    """Describe a parameter holding a load level."""
    return NumberEntityDescription(
        key=param,
        name=name,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    )


def _time(param: str, name: str) -> NumberEntityDescription:
    """Describe a parameter holding a duration."""
    return NumberEntityDescription(
        key=param,
        name=name,
        device_class=NumberDeviceClass.DURATION,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        mode=NumberMode.BOX,
        native_min_value=0,
        native_max_value=MAX_RAMP_MS,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
    )


def _is_relay(manager: Control4Manager, ieee: str) -> bool:
    """
    Return whether a device is relay-only.

    The self-reported model comes first: a relay can still have another type
    from a stored override or a missing dim code.
    """
    if manager.devices[ieee].raw.get("c4_model") in RELAY_MODELS:
        return True
    return _effective_type(manager, ieee) == DEVICE_TYPE_SWITCH


DIMMING_NUMBERS: tuple[NumberEntityDescription, ...] = (
    _level("01", "Default on level"),
    _time("02", "Click ramp up"),
    _time("03", "Click ramp down"),
    _time("04", "Hold ramp up"),
    _time("05", "Hold ramp down"),
    _level("06", "Maximum level"),
    _level("08", "Minimum level"),
    _time("09", "Cold start time"),
    _level("0a", "Cold start level"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up dimming table number entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    known: set[tuple[str, str]] = set()

    def _check_new_numbers() -> None:
        new: list[Control4DimmingNumber] = []
        for ieee, state in manager.devices.items():
            for description in DIMMING_NUMBERS:
                key = (ieee, description.key)
                if key in known or f"c4_tv_{description.key}" not in state.raw:
                    continue
                if description.key in RAMP_PARAMS and _is_relay(manager, ieee):
                    continue
                known.add(key)
                new.append(
                    Control4DimmingNumber(
                        manager=manager,
                        ieee_address=ieee,
                        friendly_name=state.friendly_name,
                        model_id=state.model_id,
                        description=description,
                    )
                )
        if new:
            async_add_entities(new)
            LOGGER.debug("Added %d dimming number entities", len(new))

    _check_new_numbers()
    manager.add_listener(_check_new_numbers)


class Control4DimmingNumber(NumberEntity):
    """One writable dimming table parameter."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        model_id: str,
        description: NumberEntityDescription,
    ) -> None:
        """Initialize the dimming number entity."""
        self.entity_description = description
        self._manager = manager
        self._ieee = ieee_address
        self._unsub_listener: Callable[[], None] | None = None
        self._attr_unique_id = f"{ieee_address}_dimming_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
            "model": model_id or None,
        }

    @property
    def native_value(self) -> float | None:
        """Return the value last read from the device."""
        device = self._manager.devices.get(self._ieee)
        if device is None:
            return None
        return device.raw.get(f"c4_tv_{self.entity_description.key}")

    async def async_set_native_value(self, value: float) -> None:
        """Write the value to the device; the read-back updates the state."""
        await self._manager.async_set_dimming(
            self._ieee, self.entity_description.key, int(value)
        )

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

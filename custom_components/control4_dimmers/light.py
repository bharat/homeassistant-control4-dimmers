"""
Light platform for Control4 Dimmers.

Creates HA light entities for LED color control on each button slot.
Each slot has an ON color and OFF color, represented as separate lights.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)

from .const import DOMAIN, LOGGER

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
    """Set up Control4 LED light entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    store = runtime["store"]

    entities: list[Control4LedLight] = []
    for ieee, config in store.devices.items():
        state = manager.devices.get(ieee)
        if state is None:
            continue
        entities.extend(
            Control4LedLight(
                manager=manager,
                ieee_address=ieee,
                friendly_name=state.friendly_name,
                slot_id=slot.slot_id,
                slot_name=slot.name,
                mode=mode,
            )
            for slot in config.slots
            for mode in ("on", "off")
        )

    if entities:
        async_add_entities(entities)
        LOGGER.debug("Added %d LED light entities", len(entities))


class Control4LedLight(LightEntity):
    """A light entity representing a Control4 button LED color."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.HS}

    def __init__(  # noqa: PLR0913
        self,
        manager: Control4Manager,
        ieee_address: str,
        friendly_name: str,
        slot_id: int,
        slot_name: str,
        mode: str,
    ) -> None:
        """Initialize the LED light entity."""
        self._manager = manager
        self._ieee = ieee_address
        self._slot_id = slot_id
        self._mode = mode
        label = slot_name or f"Button {slot_id + 1}"
        mode_label = "On" if mode == "on" else "Off"
        self._attr_unique_id = f"{ieee_address}_led_{slot_id}_{mode}"
        self._attr_name = f"{label} LED {mode_label}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ieee_address)},
            "name": friendly_name,
            "manufacturer": "Control4",
        }
        self._attr_is_on = True
        self._attr_brightness = 255
        self._attr_hs_color = (0.0, 0.0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the LED color."""
        hs = kwargs.get(ATTR_HS_COLOR, self._attr_hs_color)
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)

        self._attr_hs_color = hs
        self._attr_brightness = brightness
        self._attr_is_on = True

        color_key = f"color_button_{self._slot_id}_{self._mode}"
        payload = {
            color_key: {
                "hue": hs[0],
                "saturation": hs[1],
            },
            f"brightness_button_{self._slot_id}_{self._mode}": brightness,
        }
        await self._manager.async_send_mqtt(self._ieee, payload)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn off the LED by setting it to black."""
        self._attr_is_on = False
        self._attr_brightness = 0
        color_key = f"color_button_{self._slot_id}_{self._mode}"
        payload = {
            color_key: {"hue": 0, "saturation": 0},
            f"brightness_button_{self._slot_id}_{self._mode}": 0,
        }
        await self._manager.async_send_mqtt(self._ieee, payload)
        self.async_write_ha_state()

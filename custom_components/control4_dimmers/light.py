"""
Light platform for Control4 Dimmers.

Two kinds of light entity:
  - Control4DimmerLight: the actual dimmer load (brightness-only).
  - Control4LedLight:    LED color control on each button slot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
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
    """Set up Control4 light entities from a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return

    manager: Control4Manager = runtime["manager"]
    store = runtime["store"]

    # --- LED light entities (created from persisted config) ---
    led_entities: list[Control4LedLight] = []
    for ieee, config in store.devices.items():
        state = manager.devices.get(ieee)
        if state is None:
            continue
        led_entities.extend(
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

    if led_entities:
        async_add_entities(led_entities)
        LOGGER.debug("Added %d LED light entities", len(led_entities))

    # --- Dimmer load entities (discovered dynamically) ---
    known_dimmers: set[str] = set()

    def _check_new_dimmers() -> None:
        new: list[Control4DimmerLight] = []
        for ieee, state in manager.devices.items():
            if ieee in known_dimmers:
                continue
            if state.device_type not in LOAD_DEVICE_TYPES:
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


# ── Dimmer load entity ───────────────────────────────────────────────


class Control4DimmerLight(LightEntity):
    """A light entity representing the physical dimmer load."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_name = "Dimmer"

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

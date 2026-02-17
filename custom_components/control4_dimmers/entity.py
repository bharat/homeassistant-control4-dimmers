"""Base entity for Control4 Dimmers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from .models import DeviceConfig


class Control4Entity(Entity):
    """Base entity for Control4 devices."""

    _attr_has_entity_name = True

    def __init__(self, device_config: DeviceConfig) -> None:
        """Initialize the entity."""
        self._device_config = device_config
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_config.ieee_address)},
            "name": device_config.friendly_name,
            "manufacturer": "Control4",
            "model": device_config.effective_type,
        }

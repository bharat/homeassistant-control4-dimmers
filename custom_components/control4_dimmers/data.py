"""Custom types for integration_blueprint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import Control4DimmersApiClient
    from .coordinator import Control4DimmersDataUpdateCoordinator


type Control4DimmersConfigEntry = ConfigEntry[Control4DimmersData]


@dataclass
class Control4DimmersData:
    """Data for the Control4Dimmers integration."""

    client: Control4DimmersApiClient
    coordinator: Control4DimmersDataUpdateCoordinator
    integration: Integration

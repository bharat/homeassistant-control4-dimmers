"""DataUpdateCoordinator for integration_blueprint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Control4DimmersApiClientAuthenticationError,
    Control4DimmersApiClientError,
)

if TYPE_CHECKING:
    from .data import Control4DimmersConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class Control4DimmersDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: Control4DimmersConfigEntry

    async def _async_update_data(self) -> Any:
        """Update data via library."""
        try:
            return await self.config_entry.runtime_data.client.async_get_data()
        except Control4DimmersApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except Control4DimmersApiClientError as exception:
            raise UpdateFailed(exception) from exception

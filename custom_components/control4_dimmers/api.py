"""API Client for control4_dimmers."""


class Control4DimmersApiClientError(Exception):
    """Exception to indicate a general API error."""


class Control4DimmersApiClientCommunicationError(Control4DimmersApiClientError):
    """Exception to indicate a communication error."""


class Control4DimmersApiClientAuthenticationError(Control4DimmersApiClientError):
    """Exception to indicate an authentication error."""


class Control4DimmersApiClient:
    """Placeholder API client for future keypad configuration API."""

    def __init__(self, username: str, password: str, session) -> None:
        """Initialize placeholder API client."""
        # TODO: Implement when API is ready
        pass

    async def async_get_data(self):
        """Placeholder method for getting data from API."""
        # TODO: Implement when API is ready
        raise NotImplementedError("API not yet implemented")

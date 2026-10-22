"""Config flow for Control4 Dimmers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    AUDIT_MODES,
    CONF_AUDIT_INTERVAL,
    CONF_AUDIT_MODE,
    CONF_MQTT_TOPIC,
    DEFAULT_AUDIT_INTERVAL,
    DEFAULT_AUDIT_MODE,
    DEFAULT_MQTT_TOPIC,
    DOMAIN,
    MAX_AUDIT_INTERVAL,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


class Control4DimmersFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Control4 Dimmers."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004
    ) -> Control4DimmersOptionsFlow:
        """Return the options flow handler."""
        return Control4DimmersOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Control4 Dimmers",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_TOPIC,
                        default=DEFAULT_MQTT_TOPIC,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                },
            ),
        )


class Control4DimmersOptionsFlow(config_entries.OptionsFlowWithReload):
    """
    Options flow: MQTT topic plus the periodic config audit sweep settings.

    OptionsFlowWithReload reloads the entry when options change, so the
    manager picks up a new interval or mode without an update listener.
    """

    async def async_step_init(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show and handle the single options form."""
        if user_input is not None:
            user_input[CONF_AUDIT_INTERVAL] = int(user_input[CONF_AUDIT_INTERVAL])
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data = self.config_entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MQTT_TOPIC,
                        default=options.get(
                            CONF_MQTT_TOPIC,
                            data.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC),
                        ),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                    vol.Required(
                        CONF_AUDIT_INTERVAL,
                        default=options.get(
                            CONF_AUDIT_INTERVAL, DEFAULT_AUDIT_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=MAX_AUDIT_INTERVAL,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        ),
                    ),
                    vol.Required(
                        CONF_AUDIT_MODE,
                        default=options.get(CONF_AUDIT_MODE, DEFAULT_AUDIT_MODE),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=AUDIT_MODES,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="audit_mode",
                        ),
                    ),
                },
            ),
        )

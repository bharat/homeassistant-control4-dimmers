"""Tests for the config flow."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.control4_dimmers.const import CONF_MQTT_TOPIC, DOMAIN


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests in this module."""


@pytest.mark.asyncio
async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test that the first step shows a form with mqtt_topic."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """Test that submitting the form creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_MQTT_TOPIC: "zigbee2mqtt"},
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Control4 Dimmers"
    assert result2["data"][CONF_MQTT_TOPIC] == "zigbee2mqtt"


@pytest.mark.asyncio
async def test_user_step_custom_topic(hass: HomeAssistant) -> None:
    """Test that a custom MQTT topic is stored correctly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_MQTT_TOPIC: "my_z2m_topic"},
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_MQTT_TOPIC] == "my_z2m_topic"


@pytest.mark.asyncio
async def test_single_instance_only(hass: HomeAssistant) -> None:
    """Test that only one instance of the integration can be configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MQTT_TOPIC: "zigbee2mqtt"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_MQTT_TOPIC: "zigbee2mqtt"},
    )
    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"

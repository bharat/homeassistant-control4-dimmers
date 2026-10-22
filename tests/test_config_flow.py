"""Tests for the config flow."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.control4_dimmers.const import (
    AUDIT_MODE_REPORT,
    CONF_AUDIT_INTERVAL,
    CONF_AUDIT_MODE,
    CONF_MQTT_TOPIC,
    DOMAIN,
)


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


@pytest.mark.asyncio
async def test_options_flow_sets_audit_settings(hass: HomeAssistant) -> None:
    """
    The options flow stores the audit settings and reloads the entry.

    The entry is set up for real because OptionsFlowWithReload schedules a
    reload on submit, and that reload runs the sensor and event platforms.
    The skip_mqtt flag (same convention as test_init.py) keeps the manager
    off the broker, and unloading at the end is what keeps the platforms
    from leaving timers behind after the test.
    """
    hass.data[f"{DOMAIN}_skip_mqtt"] = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Control4 Dimmers",
        data={CONF_MQTT_TOPIC: "zigbee2mqtt"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_MQTT_TOPIC: "z2m",
            CONF_AUDIT_INTERVAL: 30.0,
            CONF_AUDIT_MODE: AUDIT_MODE_REPORT,
        },
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_MQTT_TOPIC: "z2m",
        CONF_AUDIT_INTERVAL: 30,
        CONF_AUDIT_MODE: AUDIT_MODE_REPORT,
    }

    # Let the scheduled reload finish, then check the new manager sees
    # the options rather than the original entry data.
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    manager = hass.data[DOMAIN][entry.entry_id]["manager"]
    assert manager.mqtt_topic == "z2m"
    assert manager.audit_interval == 30
    assert manager.audit_mode == AUDIT_MODE_REPORT

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

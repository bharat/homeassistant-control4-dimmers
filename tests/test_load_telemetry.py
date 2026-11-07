"""Tests for load readings and the dimming table."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.control4_dimmers.const import DOMAIN
from custom_components.control4_dimmers.manager import Control4Manager
from custom_components.control4_dimmers.models import DeviceState
from custom_components.control4_dimmers.number import (
    DIMMING_NUMBERS,
    Control4DimmingNumber,
)
from custom_components.control4_dimmers.number import (
    async_setup_entry as number_async_setup_entry,
)
from custom_components.control4_dimmers.sensor import (
    LOAD_SENSORS,
    Control4LoadSensor,
)
from custom_components.control4_dimmers.sensor import (
    async_setup_entry as sensor_async_setup_entry,
)

from .conftest import IEEE_DIMMER, IEEE_KEYPAD


def _add(manager: Control4Manager, state: DeviceState) -> None:
    manager.devices[state.ieee_address] = state


async def _setup(manager: Control4Manager, setup) -> list:
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": {"manager": manager}}}
    entry = MagicMock(entry_id="entry")
    added: list = []
    await setup(hass, entry, added.extend)
    return added


def test_rarely_used_entities_are_disabled_by_default() -> None:
    """Dimming settings and unidentified fields stay out of the way until enabled."""
    assert not any(d.entity_registry_enabled_default for d in DIMMING_NUMBERS)
    disabled = {d.key for d in LOAD_SENSORS if not d.entity_registry_enabled_default}
    assert disabled == {"c4_load_field_9", "c4_load_field_10"}


async def test_load_sensors_appear_on_first_reading(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """A device gets load sensors only once it has reported a reading."""
    _add(manager, dimmer_state)
    added = await _setup(manager, sensor_async_setup_entry)
    assert not [e for e in added if isinstance(e, Control4LoadSensor)]

    dimmer_state.update_from_mqtt({"c4_load_power": 13, "c4_load_voltage": 121})
    manager.notify_listeners()
    load = [e for e in added if isinstance(e, Control4LoadSensor)]
    assert len(load) == len(LOAD_SENSORS)
    values = {e.entity_description.key: e.native_value for e in load}
    assert values["c4_load_power"] == 13
    assert values["c4_load_voltage"] == 121

    manager.notify_listeners()
    assert len([e for e in added if isinstance(e, Control4LoadSensor)]) == len(
        LOAD_SENSORS
    )


async def test_dimming_numbers_read_and_write(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """A number shows the read value, and a write sends one command."""
    _add(manager, dimmer_state)
    dimmer_state.update_from_mqtt({"c4_tv_02": 750})
    added = await _setup(manager, number_async_setup_entry)

    assert len(added) == 1
    number = added[0]
    assert isinstance(number, Control4DimmingNumber)
    assert number.native_value == 750

    manager.async_send_mqtt = AsyncMock()
    await number.async_set_native_value(1000)
    sent = [call.args[1] for call in manager.async_send_mqtt.await_args_list]
    assert sent == [{"c4_cmd": "c4.dm.tv 00 02 000003e8"}]

    dimmer_state.update_from_mqtt({f"c4_tv_{d.key}": 0 for d in DIMMING_NUMBERS})
    manager.notify_listeners()
    assert len(added) == len(DIMMING_NUMBERS)


async def test_relay_type_gets_no_numbers(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """A relay-only switch gets no dimming settings."""
    dimmer_state.device_type = "switch"
    _add(manager, dimmer_state)
    dimmer_state.update_from_mqtt({f"c4_tv_{d.key}": 0 for d in DIMMING_NUMBERS})

    assert await _setup(manager, number_async_setup_entry) == []


async def test_relay_model_gets_no_numbers_despite_type(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """The model identifies a relay whose type says otherwise."""
    assert dimmer_state.device_type != "switch"
    _add(manager, dimmer_state)
    dimmer_state.update_from_mqtt(
        {"c4_model": "C4-SW120277"} | {f"c4_tv_{d.key}": 0 for d in DIMMING_NUMBERS}
    )

    assert await _setup(manager, number_async_setup_entry) == []


def test_relay_dimming_table_not_read(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """A relay's table isn't read, since nothing would use it."""
    _add(manager, dimmer_state)
    dimmer_state.update_from_mqtt({"c4_model": "C4-SW120277"})

    manager._maybe_read_dimming(dimmer_state)

    assert not manager._dimming_queue


def test_dimming_read_requested_once_for_load_devices(
    manager: Control4Manager,
    dimmer_state: DeviceState,
    keypad_state: DeviceState,
) -> None:
    """Only a load device with no known values is queued, and only once."""
    assert dimmer_state.ieee_address == IEEE_DIMMER
    assert keypad_state.ieee_address == IEEE_KEYPAD
    manager._hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, _name: coro.close()
    )

    manager._maybe_read_dimming(keypad_state)
    assert not manager._dimming_queue

    manager._maybe_read_dimming(dimmer_state)
    manager._dimming_task = MagicMock(done=lambda: False)
    manager._maybe_read_dimming(dimmer_state)
    assert list(manager._dimming_queue) == [IEEE_DIMMER]


def test_dimming_read_skipped_when_cached(
    manager: Control4Manager, dimmer_state: DeviceState
) -> None:
    """Values republished from Z2M's cache mean no Zigbee read is needed."""
    dimmer_state.update_from_mqtt({"c4_tv_01": 100})
    manager._maybe_read_dimming(dimmer_state)
    assert not manager._dimming_queue

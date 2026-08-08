"""Tests for read-after-write slot verification (issue #145)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers.manager import (
    VERIFY_MAX_RETRIES,
    Control4Manager,
)
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.store import Control4Store

IEEE = "0x000fff0000aaa001"
FRIENDLY = "Theater"
SLOT_ID = 3

# The slot's DESIRED config. led_mode "fixed" maps to firmware "programmed".
DESIRED_ON = "ff0000"
DESIRED_OFF = "0000ff"


def _build(mock_hass: MagicMock) -> tuple[Control4Manager, DeviceConfig]:
    """Build a manager with a real store holding one keypad slot."""
    entry = MagicMock()
    entry.data = {"mqtt_topic": "zigbee2mqtt"}
    entry.options = {}
    with patch("custom_components.control4_dimmers.store.Store"):
        store = Control4Store(mock_hass, "entry1")
    store._store.async_save = AsyncMock()
    config = DeviceConfig(
        ieee_address=IEEE,
        friendly_name=FRIENDLY,
        device_type="keypad",
        slots=[
            SlotConfig(
                slot_id=SLOT_ID,
                name="Button 3",
                behavior="keypad",
                led_mode="fixed",
                led_on_color=DESIRED_ON,
                led_off_color=DESIRED_OFF,
            )
        ],
    )
    store._devices[IEEE] = config
    mgr = Control4Manager(mock_hass, entry, store)
    mgr._devices[IEEE] = DeviceState(
        ieee_address=IEEE,
        friendly_name=FRIENDLY,
        device_type="keypad",
    )
    return mgr, config


def _response(
    *,
    on: str | None = DESIRED_ON,
    off: str | None = DESIRED_OFF,
    led_mode: str | None = "programmed",
    behavior: str | None = "keypad",
) -> dict[str, Any]:
    """Build a converter read-back payload with the correlation marker."""
    payload: dict[str, Any] = {"c4_verified_slot": SLOT_ID}
    if on is not None:
        payload[f"c4_led_{SLOT_ID}_on"] = on
    if off is not None:
        payload[f"c4_led_{SLOT_ID}_off"] = off
    if led_mode is not None:
        payload[f"button_{SLOT_ID}_led_mode"] = led_mode
    if behavior is not None:
        payload[f"button_{SLOT_ID}_behavior"] = behavior
    return payload


def _make_fake_send(
    mgr: Control4Manager,
    responses: list[dict[str, Any]],
    *,
    events: list[tuple[str, Any]] | None = None,
) -> Any:
    """
    Return a stand-in for async_send_mqtt that mocks the converter.

    When the code publishes a c4_verify_slot read, inject the next queued
    device-state response back through the same path _handle_device_state
    consumes, so the observed values land and the verify future resolves.
    Non-verify publishes (c4_cmd re-pushes) are ignored.
    """

    async def fake_send(_ieee: str, payload: dict[str, Any]) -> None:
        if "c4_verify_slot" not in payload:
            return
        if events is not None:
            events.append(("publish", payload["c4_verify_slot"]))
        if not responses:
            return
        resp = responses.pop(0)
        msg = MagicMock()
        msg.topic = f"zigbee2mqtt/{FRIENDLY}"
        msg.payload = json.dumps(resp)
        await mgr._handle_device_state(msg)

    return fake_send


@pytest.mark.asyncio
async def test_match_marks_slot_verified(mock_hass: MagicMock) -> None:
    """Observed == desired marks the slot in sync with no retry, no re-push."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    responses = [_response()]
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch.object(mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses)),
        patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push_spy,
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    push_spy.assert_not_called()
    result = mgr.get_verify_result(IEEE, SLOT_ID)
    assert result is not None
    assert result["in_sync"] is True
    assert result["last_verified"]
    assert result["observed_on_color"] == DESIRED_ON


@pytest.mark.asyncio
async def test_mismatch_then_fix(mock_hass: MagicMock) -> None:
    """A wrong first read triggers exactly one re-push, then a matching read."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    # First read observes a wrong on-color; second read (after re-push) matches.
    responses = [_response(on="00ff00"), _response()]
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch("custom_components.control4_dimmers.manager.VERIFY_RETRY_SETTLE_MS", 0),
        patch.object(mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses)),
        patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push_spy,
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    assert push_spy.call_count == 1
    result = mgr.get_verify_result(IEEE, SLOT_ID)
    assert result is not None
    assert result["in_sync"] is True


@pytest.mark.asyncio
async def test_persistent_mismatch_logs_error_and_bounds_retries(
    mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Both reads mismatch: ERROR logged, not in sync, exactly one re-push."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    responses = [_response(on="00ff00"), _response(on="00ff00")]
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch("custom_components.control4_dimmers.manager.VERIFY_RETRY_SETTLE_MS", 0),
        patch.object(mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses)),
        patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push_spy,
        caplog.at_level(logging.WARNING, logger="custom_components.control4_dimmers"),
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    # Bounded: exactly VERIFY_MAX_RETRIES re-pushes, no infinite retry.
    assert push_spy.call_count == VERIFY_MAX_RETRIES
    result = mgr.get_verify_result(IEEE, SLOT_ID)
    assert result is not None
    assert result["in_sync"] is False
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert "Verify mismatch" in caplog.text


@pytest.mark.asyncio
async def test_absent_field_is_not_a_mismatch(mock_hass: MagicMock) -> None:
    """An observed payload without button_N_behavior skips that field."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    # Behavior absent from the read (firmware did not answer it).
    responses = [_response(behavior=None)]
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch.object(mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses)),
        patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push_spy,
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    push_spy.assert_not_called()
    result = mgr.get_verify_result(IEEE, SLOT_ID)
    assert result is not None
    assert result["in_sync"] is True
    assert result["observed_behavior"] is None


@pytest.mark.asyncio
async def test_timeout_warns_and_does_not_leak(
    mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """No c4_verified_slot ever arrives: verify times out and cleans up."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    # Empty response queue: the fake send publishes nothing back.
    responses: list[dict[str, Any]] = []
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch("custom_components.control4_dimmers.manager.VERIFY_TIMEOUT_MS", 50),
        patch.object(mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses)),
        caplog.at_level(logging.WARNING, logger="custom_components.control4_dimmers"),
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    assert "timed out" in caplog.text
    # No pending future left behind, no result recorded.
    assert mgr._pending_verifies == {}
    assert mgr.get_verify_result(IEEE, SLOT_ID) is None


@pytest.mark.asyncio
async def test_settle_delay_awaited_before_publish(mock_hass: MagicMock) -> None:
    """The settle sleep is awaited before the verify read is published."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    events: list[tuple[str, Any]] = []
    responses = [_response()]

    async def fake_sleep(delay: float) -> None:
        events.append(("sleep", delay))

    with (
        patch(
            "custom_components.control4_dimmers.manager.asyncio.sleep", new=fake_sleep
        ),
        patch.object(
            mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses, events=events)
        ),
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    # First event is the settle sleep, and it precedes the verify publish.
    assert events[0] == ("sleep", 0.25)
    assert ("publish", SLOT_ID) in events
    assert events.index(("sleep", 0.25)) < events.index(("publish", SLOT_ID))


@pytest.mark.asyncio
async def test_concurrent_verify_coalesced(
    mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Two verifies for the same slot back to back: the second coalesces."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]
    events: list[tuple[str, Any]] = []
    # Only one response is queued; a second verify would find it empty and
    # time out, so the assertions below prove the second never ran.
    responses = [_response()]

    tasks: list[asyncio.Task[None]] = []

    def _create_task(coro: Any, _name: str | None = None) -> asyncio.Task[None]:
        task = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    mgr._hass.async_create_task = _create_task

    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch.object(
            mgr, "async_send_mqtt", new=_make_fake_send(mgr, responses, events=events)
        ),
        caplog.at_level(logging.DEBUG, logger="custom_components.control4_dimmers"),
    ):
        # Schedule twice in the same tick; the guard is set synchronously.
        mgr.schedule_slot_verify(state, config, SLOT_ID)
        mgr.schedule_slot_verify(state, config, SLOT_ID)
        await asyncio.gather(*tasks)

    # Only one task was ever scheduled; the second schedule coalesced.
    assert len(tasks) == 1
    assert "coalescing" in caplog.text
    # Exactly one verify publish and one recorded result.
    publishes = [e for e in events if e[0] == "publish"]
    assert len(publishes) == 1
    result = mgr.get_verify_result(IEEE, SLOT_ID)
    assert result is not None
    assert result["in_sync"] is True
    # No spurious timeout from an orphaned second future.
    assert "timed out" not in caplog.text
    # Guard and pending future are both cleared.
    assert (IEEE, SLOT_ID) not in mgr._verifying
    assert mgr._pending_verifies == {}


@pytest.mark.asyncio
async def test_device_removed_mid_verify_aborts(
    mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A device removed while the read is in flight aborts without recording."""
    mgr, config = _build(mock_hass)
    state = mgr.devices[IEEE]

    async def fake_send(_ieee: str, payload: dict[str, Any]) -> None:
        if "c4_verify_slot" not in payload:
            return
        # The device vanishes while the verify awaits its read-back, then the
        # correlated future resolves so the pass wakes to find it gone.
        mgr._devices.pop(IEEE, None)
        future = mgr._pending_verifies.get((IEEE, SLOT_ID))
        if future is not None and not future.done():
            future.set_result(None)

    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_SETTLE_MS", 0),
        patch.object(mgr, "async_send_mqtt", new=fake_send),
        caplog.at_level(logging.DEBUG, logger="custom_components.control4_dimmers"),
    ):
        await mgr._verify_slot(state, config, config.slots[0])

    # Aborted: no in_sync result recorded for the removed device.
    assert mgr.get_verify_result(IEEE, SLOT_ID) is None
    assert "removed during verify" in caplog.text
    # No leaked future or in-flight guard entry.
    assert mgr._pending_verifies == {}
    assert (IEEE, SLOT_ID) not in mgr._verifying

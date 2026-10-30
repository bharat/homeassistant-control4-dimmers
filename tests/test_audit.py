"""Tests for the periodic config audit sweep (issue #199)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from itertools import pairwise
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.control4_dimmers import _svc_audit_config
from custom_components.control4_dimmers.audit import (
    FieldReading,
    classify_drift,
    compare_three_way,
    pick_stalest,
    summarize_layers,
)
from custom_components.control4_dimmers.const import (
    AUDIT_MODE_HEAL,
    AUDIT_MODE_REPORT,
    CONF_AUDIT_INTERVAL,
    CONF_AUDIT_MODE,
    DOMAIN,
    DRIFT_LAYER_BOTH,
    DRIFT_LAYER_CACHE,
    DRIFT_LAYER_FIRMWARE,
)
from custom_components.control4_dimmers.manager import (
    AUDIT_HEAL_MAX_RETRIES,
    MIN_SEND_INTERVAL,
    Control4Manager,
)
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.sensor import Control4AuditSensor
from custom_components.control4_dimmers.store import Control4Store

IEEE_A = "0x000fff0000aaa001"
IEEE_B = "0x000fff0000bbb002"
IEEE_C = "0x000fff0000ccc003"
NAMES = {IEEE_A: "Entry", IEEE_B: "Theater", IEEE_C: "Kitchen"}

LOGGER_NAME = "custom_components.control4_dimmers"


# ── helpers ──────────────────────────────────────────────────────────


def _keypaddim_config(ieee: str, slot_count: int = 6) -> DeviceConfig:
    """Build a keypad dimmer whose slot 1 toggles the load (the incident shape)."""
    slots = [
        SlotConfig(
            slot_id=1,
            name="Lights",
            behavior="toggle_load",
            led_mode="follow_load",
            led_on_color="ffffff",
            led_off_color="000000",
        )
    ]
    slots += [
        SlotConfig(
            slot_id=i,
            name=f"Button {i}",
            behavior="keypad",
            led_mode="fixed",
            led_on_color="0000ff",
            led_off_color="000000",
        )
        for i in range(2, slot_count + 1)
    ]
    return DeviceConfig(
        ieee_address=ieee,
        friendly_name=NAMES[ieee],
        device_type="keypaddim",
        slots=slots,
    )


def _switch_config(ieee: str) -> DeviceConfig:
    """Build a relay-only switch: rocker on wires 01 and 05 (issue #189)."""
    return DeviceConfig(
        ieee_address=ieee,
        friendly_name=NAMES[ieee],
        device_type="switch",
        slots=[
            SlotConfig(
                slot_id=2,
                name="Top",
                behavior="load_on",
                led_mode="follow_load",
                led_on_color="ffffff",
                led_off_color="000000",
            ),
            SlotConfig(
                slot_id=6,
                name="Bottom",
                behavior="load_off",
                led_mode="follow_load",
                led_on_color="000000",
                led_off_color="0000ff",
            ),
        ],
    )


def _build(
    mock_hass: MagicMock,
    ieees: list[str],
    *,
    slot_count: int = 6,
    options: dict[str, Any] | None = None,
) -> Control4Manager:
    """Build a manager with a real store, one keypaddim config per IEEE."""
    entry = MagicMock()
    entry.data = {"mqtt_topic": "zigbee2mqtt"}
    entry.options = options or {}
    with patch("custom_components.control4_dimmers.store.Store"):
        store = Control4Store(mock_hass, "entry1")
    store._store.async_save = AsyncMock()
    mgr = Control4Manager(mock_hass, entry, store)
    for ieee in ieees:
        store._devices[ieee] = _keypaddim_config(ieee, slot_count)
        mgr._devices[ieee] = DeviceState(
            ieee_address=ieee,
            friendly_name=NAMES[ieee],
            device_type="keypaddim",
        )
    mock_hass.data = {DOMAIN: {"entry1": {"manager": mgr, "store": store}}}
    return mgr


def _fw_payload(
    slot_id: int,
    *,
    on: str | None = None,
    off: str | None = None,
    led_mode: str | None = None,
    behavior: str | None = None,
) -> dict[str, Any]:
    """Build a converter read-back payload for one slot (absent keys = unread)."""
    payload: dict[str, Any] = {"c4_verified_slot": slot_id}
    if on is not None:
        payload[f"c4_led_{slot_id}_on"] = on
    if off is not None:
        payload[f"c4_led_{slot_id}_off"] = off
    if led_mode is not None:
        payload[f"button_{slot_id}_led_mode"] = led_mode
    if behavior is not None:
        payload[f"button_{slot_id}_behavior"] = behavior
    return payload


def _matching_payload(slot: SlotConfig) -> dict[str, Any]:
    """Build a read-back that matches the stored slot exactly."""
    return _fw_payload(
        slot.slot_id,
        on=slot.led_on_color,
        off=slot.led_off_color,
        led_mode="programmed" if slot.led_mode == "fixed" else slot.led_mode,
        behavior=slot.behavior,
    )


class _FakeDevice:
    """
    Stand-in for the converter on the far side of async_send_mqtt.

    firmware maps slot_id -> read-back payload. On a c4_verify_slot read
    the payload is injected back through _handle_device_state. On a
    c4.dmx.btn / c4.dmx.led re-push (a heal) the firmware copy is updated
    to the stored slot, so the follow-up read matches. Every call is
    recorded in events for ordering assertions.
    """

    def __init__(
        self,
        mgr: Control4Manager,
        ieee: str,
        firmware: dict[int, dict[str, Any]],
        *,
        heal_fixes: bool = True,
    ) -> None:
        self.mgr = mgr
        self.ieee = ieee
        self.firmware = firmware
        self.heal_fixes = heal_fixes
        self.events: list[tuple[str, Any]] = []

    async def send(self, ieee: str, payload: dict[str, Any]) -> None:
        if "c4_verify_slot" in payload:
            slot_id = payload["c4_verify_slot"]
            self.events.append(("read", slot_id))
            resp = self.firmware.get(slot_id)
            if resp is None:
                return  # no answer: the read times out
            msg = MagicMock()
            msg.topic = f"zigbee2mqtt/{NAMES[ieee]}"
            msg.payload = json.dumps(resp)
            await self.mgr._handle_device_state(msg)
            return
        if "c4_cmd" in payload:
            self.events.append(("push", payload["c4_cmd"]))
            if self.heal_fixes:
                wire = int(payload["c4_cmd"].split()[1], 16)
                slot = self.mgr.store.get_device(ieee).slots[wire]
                assert slot.slot_id == wire + 1
                self.firmware[slot.slot_id] = _matching_payload(slot)
            return
        self.events.append(("state_write", payload))


def _result(mgr: Control4Manager, ieee: str, slot_id: int) -> dict[str, Any]:
    res = mgr.get_verify_result(ieee, slot_id)
    assert res is not None
    return res


@contextmanager
def _fast() -> Iterator[None]:
    """Shrink the heal settle and the read timeout so tests run fast."""
    with (
        patch("custom_components.control4_dimmers.manager.VERIFY_RETRY_SETTLE_MS", 0),
        patch("custom_components.control4_dimmers.manager.VERIFY_TIMEOUT_MS", 50),
    ):
        yield


# ── pure helpers: three-way classification ───────────────────────────


class TestClassifyDrift:
    @pytest.mark.parametrize(
        ("store", "cache", "fw", "expected"),
        [
            ("toggle_load", "toggle_load", "toggle_load", None),
            # cache agrees with the store, firmware does not: firmware drift
            ("toggle_load", "toggle_load", "load_off", DRIFT_LAYER_FIRMWARE),
            # firmware agrees with the store, cache does not: cache-only drift
            ("toggle_load", "load_off", "toggle_load", DRIFT_LAYER_CACHE),
            # both layers agree with each other but not the store
            ("toggle_load", "load_off", "load_off", DRIFT_LAYER_BOTH),
            # all three differ
            ("toggle_load", "load_on", "load_off", DRIFT_LAYER_BOTH),
            # a layer with no value is skipped, not counted as drift
            ("toggle_load", None, "toggle_load", None),
            ("toggle_load", None, "load_off", DRIFT_LAYER_FIRMWARE),
            ("toggle_load", "load_off", None, DRIFT_LAYER_CACHE),
            ("toggle_load", None, None, None),
        ],
    )
    def test_matrix(self, store, cache, fw, expected) -> None:
        assert classify_drift(FieldReading(store, cache, fw)) == expected

    def test_compare_maps_fixed_to_programmed_and_normalizes_colors(self) -> None:
        slot = SlotConfig(
            slot_id=3,
            behavior="keypad",
            led_mode="fixed",
            led_on_color="FF0000",
            led_off_color="0000ff",
        )
        mismatches = compare_three_way(
            slot,
            cached_led={"on": "#ff0000", "off": "0000FF"},
            cached_btn={"led_mode": "programmed", "behavior": "keypad"},
            fw_led={"on": "ff0000", "off": "0000ff"},
            fw_btn={"led_mode": "programmed", "behavior": "keypad"},
        )
        assert mismatches == []

    def test_compare_reports_every_drifted_field(self) -> None:
        slot = SlotConfig(slot_id=1, behavior="toggle_load", led_mode="follow_load")
        mismatches = compare_three_way(
            slot,
            cached_led={},
            cached_btn={"behavior": "load_off"},
            fw_led={"on": "123456"},
            fw_btn={"behavior": "toggle_load", "led_mode": "programmed"},
        )
        by_field = {m.field: m for m in mismatches}
        assert set(by_field) == {"on_color", "led_mode", "behavior"}
        assert by_field["behavior"].layer == DRIFT_LAYER_CACHE
        assert by_field["behavior"].cache == "load_off"
        assert by_field["behavior"].firmware == "toggle_load"
        assert by_field["led_mode"].layer == DRIFT_LAYER_FIRMWARE
        assert by_field["on_color"].layer == DRIFT_LAYER_FIRMWARE
        assert by_field["on_color"].cache is None
        assert summarize_layers(mismatches) == DRIFT_LAYER_BOTH
        assert summarize_layers([by_field["behavior"]]) == DRIFT_LAYER_CACHE
        assert summarize_layers([]) is None


class TestPickStalest:
    def test_never_verified_goes_first(self) -> None:
        now = dt_util.utcnow()
        assert pick_stalest({IEEE_A: now - timedelta(days=9), IEEE_B: None}) == IEEE_B

    def test_oldest_wins_among_verified(self) -> None:
        now = dt_util.utcnow()
        candidates = {
            IEEE_A: now - timedelta(hours=1),
            IEEE_B: now - timedelta(hours=5),
            IEEE_C: now - timedelta(minutes=10),
        }
        assert pick_stalest(candidates) == IEEE_B

    def test_ties_break_on_ieee(self) -> None:
        assert pick_stalest({IEEE_C: None, IEEE_A: None, IEEE_B: None}) == IEEE_A

    def test_empty(self) -> None:
        assert pick_stalest({}) is None


# ── scheduler: picks the stalest device, respects pushes ─────────────


class TestScheduler:
    def test_never_verified_device_before_a_stale_one(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=2)
        old = dt_util.utcnow() - timedelta(days=3)
        # Device A fully verified long ago; device B never.
        for slot_id in (1, 2):
            mgr._verify_results[(IEEE_A, slot_id)] = {"last_verified_at": old}
        assert mgr.pick_audit_target() == IEEE_B

    def test_stalest_slot_defines_device_age(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=2)
        now = dt_util.utcnow()
        # A: one fresh slot, one old slot. B: both moderately old.
        mgr._verify_results[(IEEE_A, 1)] = {"last_verified_at": now}
        mgr._verify_results[(IEEE_A, 2)] = {"last_verified_at": now - timedelta(days=2)}
        for slot_id in (1, 2):
            mgr._verify_results[(IEEE_B, slot_id)] = {
                "last_verified_at": now - timedelta(hours=6)
            }
        assert mgr.pick_audit_target() == IEEE_A

    def test_partially_verified_device_counts_as_never(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=2)
        now = dt_util.utcnow()
        mgr._verify_results[(IEEE_A, 1)] = {"last_verified_at": now}
        for slot_id in (1, 2):
            mgr._verify_results[(IEEE_B, slot_id)] = {
                "last_verified_at": now - timedelta(days=30)
            }
        # A has an unverified slot, so it is a coverage hole and goes first.
        assert mgr.pick_audit_target() == IEEE_A

    def test_attempted_device_yields_its_turn(self, mock_hass: MagicMock) -> None:
        """A device whose reads time out must not be picked every tick."""
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=2)
        now = dt_util.utcnow()
        # Both never verified; A was attempted a moment ago (reads timed out).
        mgr._audit_attempts[IEEE_A] = now
        assert mgr.pick_audit_target() == IEEE_B
        # Once B has been attempted too, the older attempt (A) comes back.
        mgr._audit_attempts[IEEE_B] = now + timedelta(seconds=1)
        assert mgr.pick_audit_target() == IEEE_A

    def test_skips_device_with_push_in_flight(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=2)
        lock = mgr._device_lock(mgr._push_locks, IEEE_A)

        async def _check() -> None:
            async with lock:
                assert mgr.pick_audit_target() == IEEE_B
            assert mgr.pick_audit_target() == IEEE_A

        asyncio.run(_check())

    def test_devices_without_config_are_not_candidates(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=2)
        mgr._devices[IEEE_B] = DeviceState(ieee_address=IEEE_B, friendly_name="Theater")
        assert set(mgr._audit_candidates()) == {IEEE_A}

    def test_start_arms_timer_and_stop_cancels(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], options={CONF_AUDIT_INTERVAL: 7})
        unsub = MagicMock()
        with patch(
            "custom_components.control4_dimmers.manager.async_track_time_interval",
            return_value=unsub,
        ) as track:
            mgr.start_audit_sweep()
        track.assert_called_once()
        assert track.call_args.args[2] == timedelta(minutes=7)
        mgr.stop_audit_sweep()
        unsub.assert_called_once()
        assert mgr._audit_unsub is None

    def test_interval_zero_disables(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], options={CONF_AUDIT_INTERVAL: 0})
        with patch(
            "custom_components.control4_dimmers.manager.async_track_time_interval"
        ) as track:
            mgr.start_audit_sweep()
        track.assert_not_called()
        assert mgr.audit_interval == 0

    def test_option_fallbacks(self, mock_hass: MagicMock) -> None:
        mgr = _build(
            mock_hass,
            [IEEE_A],
            options={CONF_AUDIT_INTERVAL: "junk", CONF_AUDIT_MODE: "x"},
        )
        assert mgr.audit_interval == 10
        assert mgr.audit_mode == AUDIT_MODE_HEAL
        # Options that lack mqtt_topic fall back to entry data, not the default.
        mgr._entry.data = {"mqtt_topic": "custom"}
        assert mgr.mqtt_topic == "custom"

    @pytest.mark.asyncio
    async def test_tick_schedules_task_and_guards_overlap(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A])
        created: list[Any] = []

        def _create(coro: Any, _name: str | None = None) -> None:
            coro.close()
            created.append(coro)

        mock_hass.async_create_task = _create
        mgr._audit_tick(dt_util.utcnow())
        assert len(created) == 1
        mgr._audit_running = True
        mgr._audit_tick(dt_util.utcnow())
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_audit_next_runs_the_picked_device(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=2)
        config = mgr.store.get_device(IEEE_A)
        fake = _FakeDevice(
            mgr, IEEE_A, {s.slot_id: _matching_payload(s) for s in config.slots}
        )
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await mgr.async_audit_next()
        assert summary is not None
        assert summary["ieee_address"] == IEEE_A
        assert summary["slots_checked"] == 2
        assert [e for e in fake.events if e[0] == "read"] == [("read", 1), ("read", 2)]
        # Every slot is now verified by the audit, so nothing is a hole.
        assert mgr.get_audit_status()["devices_never_verified"] == 0

    @pytest.mark.asyncio
    async def test_audit_next_idle_without_devices(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [])
        assert await mgr.async_audit_next() is None


# ── the audit pass: three-way verdicts, heal, report, evidence first ─


class TestAuditPass:
    @pytest.mark.asyncio
    async def test_in_sync_records_audit_verify(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["slots_mismatched"] == 0
        assert not [e for e in fake.events if e[0] == "push"]
        res = _result(mgr, IEEE_A, 1)
        assert res["in_sync"] is True
        assert res["verified_by"] == "audit"
        assert res["diverged_layer"] is None

    @pytest.mark.asyncio
    async def test_cache_only_drift_is_classified_and_healed(
        self, mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The incident shape: Z2M cache says load_off, firmware says toggle."""
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        # What the integration last saw Z2M publish for button_1_*.
        mgr._devices[IEEE_A].button_configs[1] = {
            "behavior": "load_off",
            "led_mode": "follow_load",
        }
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
            caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        ):
            summary = await mgr.async_audit_device(IEEE_A)

        assert summary["slots_mismatched"] == 1
        assert summary["slots_healed"] == 1
        assert summary["mismatches"] == [
            {
                "slot_id": 1,
                "layer": DRIFT_LAYER_CACHE,
                "field": "behavior",
                "store": "toggle_load",
                "cache": "load_off",
                "firmware": "toggle_load",
            }
        ]
        line = next(r for r in caplog.records if "Audit mismatch" in r.getMessage())
        assert line.levelno == logging.WARNING
        assert (
            "field=behavior store=toggle_load cache=load_off "
            "firmware=toggle_load layer=cache mode=heal" in line.getMessage()
        )
        # Heal re-pushed slot 1 (wire 00) including the Z2M state write that
        # refreshes the cached behavior.
        pushes = [e[1] for e in fake.events if e[0] == "push"]
        assert pushes
        assert all(" 00 " in c for c in pushes)
        assert any(
            e[0] == "state_write" and e[1].get("button_1_behavior") == "toggle_load"
            for e in fake.events
        )
        res = _result(mgr, IEEE_A, 1)
        assert res["in_sync"] is True
        assert res["diverged_layer"] is None

    @pytest.mark.asyncio
    async def test_firmware_drift_is_classified(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        # Cache agrees with the store; firmware answers load_off.
        mgr._devices[IEEE_A].button_configs[1] = {
            "behavior": "toggle_load",
            "led_mode": "follow_load",
        }
        drifted = _fw_payload(
            1, on="ffffff", off="000000", led_mode="follow_load", behavior="load_off"
        )
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["mismatches"][0]["layer"] == DRIFT_LAYER_FIRMWARE
        assert summary["slots_healed"] == 1

    @pytest.mark.asyncio
    async def test_both_layers_drifted(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        mgr._devices[IEEE_A].button_configs[1] = {"behavior": "load_off"}
        drifted = _fw_payload(
            1, on="ffffff", off="000000", led_mode="follow_load", behavior="load_off"
        )
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["mismatches"][0]["layer"] == DRIFT_LAYER_BOTH

    @pytest.mark.asyncio
    async def test_cache_snapshot_taken_before_read(self, mock_hass: MagicMock) -> None:
        """The read refreshes the mirror; the verdict must use the pre-read copy."""
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        mgr._devices[IEEE_A].button_configs[1] = {"behavior": "load_off"}
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        # After the read the mirror holds the firmware value...
        assert mgr._devices[IEEE_A].button_configs[1]["behavior"] == "toggle_load"
        # ...but the verdict still names the cache as the drifted layer.
        assert summary["mismatches"][0]["cache"] == "load_off"
        assert summary["mismatches"][0]["layer"] == DRIFT_LAYER_CACHE

    @pytest.mark.asyncio
    async def test_report_mode_logs_but_does_not_heal(
        self, mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mgr = _build(
            mock_hass,
            [IEEE_A],
            slot_count=1,
            options={CONF_AUDIT_MODE: AUDIT_MODE_REPORT},
        )
        mgr._devices[IEEE_A].button_configs[1] = {"behavior": "load_off"}
        drifted = _fw_payload(1, behavior="load_off", led_mode="follow_load")
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
            patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push,
            caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        push.assert_not_called()
        assert [e for e in fake.events if e[0] == "push"] == []
        assert summary["mode"] == AUDIT_MODE_REPORT
        assert summary["slots_mismatched"] == 1
        assert summary["slots_healed"] == 0
        assert summary["slots_unhealed"] == 0
        assert "layer=both mode=report" in caplog.text
        res = _result(mgr, IEEE_A, 1)
        assert res["in_sync"] is False
        assert res["diverged_layer"] == DRIFT_LAYER_BOTH
        # The mirror still reflects what firmware answered (evidence kept).
        assert mgr._devices[IEEE_A].button_configs[1]["behavior"] == "load_off"

    @pytest.mark.asyncio
    async def test_mode_override_per_call(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)  # configured: heal
        drifted = _fw_payload(1, behavior="load_off")
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
        ):
            summary = await mgr.async_audit_device(IEEE_A, mode=AUDIT_MODE_REPORT)
        assert summary["mode"] == AUDIT_MODE_REPORT
        assert [e for e in fake.events if e[0] == "push"] == []

    @pytest.mark.asyncio
    async def test_evidence_logged_before_heal(self, mock_hass: MagicMock) -> None:
        """Every mismatch WARNING precedes the first re-push command."""
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        mgr._devices[IEEE_A].button_configs[1] = {"behavior": "load_off"}
        drifted = _fw_payload(1, behavior="load_off", led_mode="programmed")
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})

        timeline: list[tuple[str, str]] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                timeline.append(("log", record.getMessage()))

        original_send = fake.send

        async def _send(ieee: str, payload: dict[str, Any]) -> None:
            if "c4_cmd" in payload:
                timeline.append(("push", payload["c4_cmd"]))
            await original_send(ieee, payload)

        handler = _Capture(level=logging.WARNING)
        logger = logging.getLogger(LOGGER_NAME)
        logger.addHandler(handler)
        try:
            with (
                _fast(),
                patch.object(mgr, "async_send_mqtt", new=_send),
            ):
                await mgr.async_audit_device(IEEE_A)
        finally:
            logger.removeHandler(handler)

        first_push = next(i for i, e in enumerate(timeline) if e[0] == "push")
        mismatch_logs = [
            i
            for i, e in enumerate(timeline)
            if e[0] == "log" and "Audit mismatch" in e[1]
        ]
        verdict = next(
            i
            for i, e in enumerate(timeline)
            if e[0] == "log" and "Audit verdict" in e[1]
        )
        # Two drifted fields (behavior, led_mode), both logged before any push.
        assert len(mismatch_logs) == 2
        assert all(i < first_push for i in mismatch_logs)
        assert verdict < first_push

    @pytest.mark.asyncio
    async def test_persistent_mismatch_logs_error_and_bounds_pushes(
        self, mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        drifted = _fw_payload(1, behavior="load_off")
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted}, heal_fixes=False)
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
            patch.object(mgr, "_push_single_slot", wraps=mgr._push_single_slot) as push,
            caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        assert push.call_count == AUDIT_HEAL_MAX_RETRIES
        assert summary["slots_unhealed"] == 1
        assert any(
            r.levelno == logging.ERROR and "Audit heal failed" in r.getMessage()
            for r in caplog.records
        )
        res = _result(mgr, IEEE_A, 1)
        assert res["in_sync"] is False
        assert res["diverged_layer"] == DRIFT_LAYER_FIRMWARE

    @pytest.mark.asyncio
    async def test_dropped_write_heals_on_second_repush_in_same_pass(
        self, mock_hass: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        A dropped first re-push heals on the second attempt (issue #212).

        The first re-push never reaches firmware (mesh drop); the second
        one lands. The slot must heal within the same audit pass instead
        of waiting a full audit interval for the next cycle.
        """
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        drifted = _fw_payload(1, behavior="load_off")
        fake = _FakeDevice(mgr, IEEE_A, {1: drifted})
        real_push = mgr._push_single_slot
        calls = 0

        async def flaky_push(state: Any, config: Any, slot_id: int) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                return True  # dropped by the mesh: nothing reaches firmware
            return await real_push(state, config, slot_id)

        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
            patch.object(mgr, "_push_single_slot", new=flaky_push),
            caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        assert calls == 2
        assert summary["slots_healed"] == 1
        assert summary["slots_unhealed"] == 0
        assert not any(r.levelno == logging.ERROR for r in caplog.records)
        assert any("Audit healed" in r.getMessage() for r in caplog.records)
        res = _result(mgr, IEEE_A, 1)
        assert res["in_sync"] is True

    @pytest.mark.asyncio
    async def test_unread_slot_counts_and_does_not_record(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=2)
        slot2 = mgr.store.get_device(IEEE_A).slots[1]
        # Slot 1 never answers; slot 2 matches.
        fake = _FakeDevice(mgr, IEEE_A, {2: _matching_payload(slot2)})
        with (
            _fast(),
            patch.object(mgr, "async_send_mqtt", new=fake.send),
        ):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["slots_unread"] == 1
        assert summary["slots_checked"] == 1
        assert mgr.get_verify_result(IEEE_A, 1) is None
        assert _result(mgr, IEEE_A, 2)["in_sync"] is True
        # The attempt is recorded, so the device yields its next turn.
        assert IEEE_A in mgr._audit_attempts
        assert mgr._pending_verifies == {}
        assert mgr._verifying == set()

    @pytest.mark.asyncio
    async def test_skips_slot_with_write_verify_in_flight(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=2)
        config = mgr.store.get_device(IEEE_A)
        fake = _FakeDevice(
            mgr, IEEE_A, {s.slot_id: _matching_payload(s) for s in config.slots}
        )
        mgr._verifying.add((IEEE_A, 1))
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["slots_skipped"] == 1
        assert [e for e in fake.events if e[0] == "read"] == [("read", 2)]
        # The guard we planted is untouched; our own guards are cleared.
        assert mgr._verifying == {(IEEE_A, 1)}

    @pytest.mark.asyncio
    async def test_switch_audits_slots_only_no_level_writes(
        self, mock_hass: MagicMock
    ) -> None:
        # A relay-only switch is audited exactly like any other load-bearing
        # device: per-slot LED and behavior reads, never a state or
        # brightness push (issue #189).
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        mgr.store._devices[IEEE_A] = _switch_config(IEEE_A)
        mgr._devices[IEEE_A].device_type = "switch"
        slots = mgr.store.get_device(IEEE_A).slots
        fake = _FakeDevice(
            mgr, IEEE_A, {s.slot_id: _matching_payload(s) for s in slots}
        )
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["slots_mismatched"] == 0
        assert [e for e in fake.events if e[0] == "read"] == [
            ("read", 2),
            ("read", 6),
        ]
        assert not [e for e in fake.events if e[0] == "push"]
        assert not [e for e in fake.events if e[0] == "state_write"]
        for slot_id in (2, 6):
            assert _result(mgr, IEEE_A, slot_id)["in_sync"] is True

    @pytest.mark.asyncio
    async def test_switch_heal_pushes_slot_commands_only(
        self, mock_hass: MagicMock
    ) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        mgr.store._devices[IEEE_A] = _switch_config(IEEE_A)
        mgr._devices[IEEE_A].device_type = "switch"
        top, bottom = mgr.store.get_device(IEEE_A).slots
        firmware = {
            2: _fw_payload(
                2, on="123456", off="000000", led_mode="follow_load", behavior="load_on"
            ),
            6: _matching_payload(bottom),
        }
        fake = _FakeDevice(mgr, IEEE_A, firmware, heal_fixes=False)
        with _fast(), patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await mgr.async_audit_device(IEEE_A)
        assert summary["slots_mismatched"] == 1
        pushes = [e[1] for e in fake.events if e[0] == "push"]
        assert pushes
        # Every heal command re-pushes the wire-01 slot's own config.
        assert all(cmd.startswith(("c4.dmx.btn 01", "c4.dmx.led 01")) for cmd in pushes)
        writes = [e[1] for e in fake.events if e[0] == "state_write"]
        assert all("brightness" not in w and "state" not in w for w in writes)
        assert _result(mgr, IEEE_A, 2)["in_sync"] is False
        assert _result(mgr, IEEE_A, 6)["in_sync"] is True
        assert top.slot_id == 2

    @pytest.mark.asyncio
    async def test_unknown_device_returns_none(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A])
        assert await mgr.async_audit_device(IEEE_B) is None

    @pytest.mark.asyncio
    async def test_removal_prunes_audit_state(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            await mgr.async_audit_device(IEEE_A)
        assert IEEE_A in mgr._audit_attempts
        assert IEEE_A in mgr._audit_summaries
        msg = MagicMock()
        msg.topic = "zigbee2mqtt/bridge/devices"
        msg.payload = json.dumps([])
        await mgr._handle_bridge_devices(msg)
        assert IEEE_A not in mgr._audit_attempts
        assert IEEE_A not in mgr._audit_summaries
        assert mgr._verify_reads == {}


# ── pacing and serialization against the real send path ──────────────


class TestAuditPacing:
    @pytest.mark.asyncio
    async def test_reads_are_paced_and_serialized(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=6)
        config = mgr.store.get_device(IEEE_A)
        firmware = {s.slot_id: _matching_payload(s) for s in config.slots}
        starts: list[float] = []
        active = 0
        max_active = 0

        async def _publish(_hass: Any, _topic: str, payload: str, qos: int = 0) -> None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            starts.append(time.monotonic())
            data = json.loads(payload)
            if "c4_verify_slot" in data:
                msg = MagicMock()
                msg.topic = f"zigbee2mqtt/{NAMES[IEEE_A]}"
                msg.payload = json.dumps(firmware[data["c4_verify_slot"]])
                await mgr._handle_device_state(msg)
            active -= 1

        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=_publish,
        ):
            summary = await mgr.async_audit_device(IEEE_A)

        assert summary["slots_checked"] == 6
        assert len(starts) == 6
        assert max_active == 1
        gaps = [b - a for a, b in pairwise(starts)]
        assert all(g >= MIN_SEND_INTERVAL * 0.9 for g in gaps), gaps

    @pytest.mark.asyncio
    async def test_read_waits_for_in_flight_push(self, mock_hass: MagicMock) -> None:
        """A config push holding the device's push lock blocks the audit read."""
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        lock = mgr._device_lock(mgr._push_locks, IEEE_A)
        released = asyncio.Event()

        async def _push() -> None:
            async with lock:
                await asyncio.sleep(0.05)
                assert fake.events == []  # no read while the push holds the lock
                released.set()

        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            pusher = asyncio.create_task(_push())
            await asyncio.sleep(0)  # let the pusher take the lock first
            summary = await mgr.async_audit_device(IEEE_A)
            await pusher
        assert released.is_set()
        assert summary["slots_checked"] == 1


# ── coverage status, sensor, service ─────────────────────────────────


class TestCoverage:
    def test_status_counts(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B, IEEE_C], slot_count=2)
        now = dt_util.utcnow()
        # A: fully verified 90 min ago, one slot out of sync.
        mgr._verify_results[(IEEE_A, 1)] = {
            "in_sync": True,
            "last_verified_at": now - timedelta(minutes=90),
        }
        mgr._verify_results[(IEEE_A, 2)] = {
            "in_sync": False,
            "last_verified_at": now - timedelta(minutes=30),
        }
        # B: partially verified. C: never.
        mgr._verify_results[(IEEE_B, 1)] = {"in_sync": True, "last_verified_at": now}
        status = mgr.get_audit_status()
        assert status["devices_total"] == 3
        assert status["devices_verified"] == 1
        assert status["devices_never_verified"] == 2
        assert status["never_verified"] == ["Kitchen", "Theater"]
        assert status["out_of_sync"] == ["Entry slot 2"]
        assert status["stalest_device"] == "Entry"
        assert status["stalest_age_minutes"] == 90
        assert status["interval_minutes"] == 10
        assert status["mode"] == AUDIT_MODE_HEAL
        assert status["next_audit_device"] in ("Theater", "Kitchen")
        assert status["last_audit_device"] is None

    def test_sensor_reflects_status(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A, IEEE_B], slot_count=1)
        sensor = Control4AuditSensor(mgr, "entry1")
        assert sensor.unique_id == "entry1_config_audit"
        assert sensor.native_value == 2
        mgr._verify_results[(IEEE_A, 1)] = {
            "in_sync": True,
            "last_verified_at": dt_util.utcnow(),
        }
        assert sensor.native_value == 1
        attrs = sensor.extra_state_attributes
        assert attrs["never_verified"] == ["Theater"]
        assert attrs["stalest_device"] == "Entry"
        sensor.async_write_ha_state = MagicMock()
        sensor._on_manager_update()
        sensor.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_config_service(self, mock_hass: MagicMock) -> None:
        mgr = _build(mock_hass, [IEEE_A], slot_count=1)
        slot = mgr.store.get_device(IEEE_A).slots[0]
        fake = _FakeDevice(mgr, IEEE_A, {1: _matching_payload(slot)})
        call = MagicMock()
        call.data = {"ieee_address": IEEE_A, "mode": AUDIT_MODE_REPORT}
        with patch.object(mgr, "async_send_mqtt", new=fake.send):
            summary = await _svc_audit_config(mock_hass, call)
        assert summary["ieee_address"] == IEEE_A
        assert summary["mode"] == AUDIT_MODE_REPORT
        assert summary["slots_checked"] == 1

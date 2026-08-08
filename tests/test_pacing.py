"""Tests for per-device send pacing and serialization (issue #144)."""

from __future__ import annotations

import asyncio
import json
import time
from itertools import pairwise
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers import _svc_set_slot_led
from custom_components.control4_dimmers.const import DOMAIN
from custom_components.control4_dimmers.manager import (
    MIN_SEND_INTERVAL,
    Control4Manager,
)
from custom_components.control4_dimmers.models import (
    DeviceConfig,
    DeviceState,
    SlotConfig,
)
from custom_components.control4_dimmers.store import Control4Store

IEEE_A = "0x000fff0000aaa001"
IEEE_B = "0x000fff0000bbb002"


class _PublishRecorder:
    """Records mqtt.async_publish calls with timing and concurrency."""

    def __init__(self, hold: float = 0.005) -> None:
        self.hold = hold
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.active = 0
        self.max_active = 0

    async def publish(self, _hass: Any, topic: str, payload: str, qos: int = 0) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((topic, json.loads(payload), time.monotonic()))
        await asyncio.sleep(self.hold)
        self.active -= 1

    def starts_for(self, name: str) -> list[float]:
        """Start timestamps for publishes to the device with this name."""
        needle = f"/{name}/set"
        return [t for topic, _payload, t in self.calls if topic.endswith(needle)]


def _keypad_config(ieee: str) -> DeviceConfig:
    return DeviceConfig(
        ieee_address=ieee,
        friendly_name="Theater",
        device_type="keypad",
        slots=[
            SlotConfig(
                slot_id=i, name=f"Button {i}", behavior="keypad", led_mode="fixed"
            )
            for i in range(1, 7)
        ],
    )


def _manager_with_device(
    mock_hass: MagicMock,
    *,
    ieee: str,
    friendly_name: str,
    config: DeviceConfig | None = None,
) -> tuple[Control4Manager, Control4Store]:
    """Build a real manager + store with one discovered device."""
    entry = MagicMock()
    entry.data = {"mqtt_topic": "zigbee2mqtt"}
    entry.options = {}
    with patch("custom_components.control4_dimmers.store.Store"):
        store = Control4Store(mock_hass, "entry1")
    store._store.async_save = AsyncMock()
    if config is not None:
        store._devices[ieee] = config
    mgr = Control4Manager(mock_hass, entry, store)
    mgr._devices[ieee] = DeviceState(
        ieee_address=ieee,
        friendly_name=friendly_name,
        device_type="keypad",
    )
    return mgr, store


def _entity_state(ieee: str, slot_id: int) -> MagicMock:
    state = MagicMock()
    state.attributes = {"ieee_address": ieee, "slot_id": slot_id}
    return state


# ── (a) Same-device sends are serialized and paced ───────────────────


class TestSameDevicePacing:
    """Two rapid set_slot_led calls to one device must serialize and pace."""

    @pytest.mark.asyncio
    async def test_two_set_slot_led_calls_serialize_and_pace(
        self, mock_hass: MagicMock
    ) -> None:
        config = _keypad_config(IEEE_A)
        mgr, store = _manager_with_device(
            mock_hass, ieee=IEEE_A, friendly_name="Theater", config=config
        )
        mock_hass.data = {DOMAIN: {"entry1": {"manager": mgr, "store": store}}}
        mock_hass.states.async_all.return_value = []

        rec = _PublishRecorder()

        def _state_for(entity_id: str) -> MagicMock:
            slot_id = 1 if entity_id.endswith("_1") else 2
            return _entity_state(IEEE_A, slot_id)

        mock_hass.states.get.side_effect = _state_for

        call_1 = MagicMock()
        call_1.data = {"entity_id": "event.theater_1", "on_color": "00ff00"}
        call_2 = MagicMock()
        call_2.data = {"entity_id": "event.theater_2", "off_color": "ff0000"}

        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=rec.publish,
        ):
            await asyncio.gather(
                _svc_set_slot_led(mock_hass, call_1),
                _svc_set_slot_led(mock_hass, call_2),
            )

        # Serialized: publishes to the same device never overlap.
        assert rec.max_active == 1
        # Paced: consecutive sends to the device are >= MIN_SEND_INTERVAL apart.
        starts = rec.starts_for("Theater")
        assert len(starts) > 1
        gaps = [b - a for a, b in pairwise(starts)]
        tolerance = MIN_SEND_INTERVAL * 0.9
        assert all(g >= tolerance for g in gaps), gaps

    @pytest.mark.asyncio
    async def test_async_send_mqtt_paces_consecutive_sends(
        self, mock_hass: MagicMock
    ) -> None:
        mgr, _ = _manager_with_device(mock_hass, ieee=IEEE_A, friendly_name="Theater")
        rec = _PublishRecorder(hold=0.0)
        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=rec.publish,
        ):
            for i in range(4):
                await mgr.async_send_mqtt(
                    IEEE_A, {"c4_cmd": f"c4.dmx.led 0{i} 05 ffffff"}
                )
        starts = rec.starts_for("Theater")
        gaps = [b - a for a, b in pairwise(starts)]
        assert all(g >= MIN_SEND_INTERVAL * 0.9 for g in gaps), gaps


# ── (b) Different-device sends are independent ───────────────────────


class TestCrossDeviceIndependence:
    """Sends to two different devices must not serialize against each other."""

    @pytest.mark.asyncio
    async def test_two_devices_overlap(self, mock_hass: MagicMock) -> None:
        mgr_a, _ = _manager_with_device(mock_hass, ieee=IEEE_A, friendly_name="DeviceA")
        # A single manager can only hold one device map; register both here.
        mgr_a._devices[IEEE_B] = DeviceState(
            ieee_address=IEEE_B, friendly_name="DeviceB", device_type="keypad"
        )
        rec = _PublishRecorder(hold=0.02)

        async def _spam(ieee: str) -> None:
            for i in range(3):
                await mgr_a.async_send_mqtt(
                    ieee, {"c4_cmd": f"c4.dmx.led 0{i} 05 ffffff"}
                )

        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=rec.publish,
        ):
            await asyncio.gather(_spam(IEEE_A), _spam(IEEE_B))

        # Separate send locks: publishes to the two devices overlap in time.
        assert rec.max_active >= 2


# ── (c) Changed-slot-only path emits just one slot's commands ────────


class TestSingleSlotPush:
    """_push_single_slot must emit only the changed slot's commands."""

    @pytest.mark.asyncio
    async def test_pushes_only_the_changed_slot(self, mock_hass: MagicMock) -> None:
        config = _keypad_config(IEEE_A)
        mgr, _ = _manager_with_device(
            mock_hass, ieee=IEEE_A, friendly_name="Theater", config=config
        )
        state = mgr.devices[IEEE_A]
        with patch.object(mgr, "async_send_mqtt", new_callable=AsyncMock) as mock_send:
            ok = await mgr._push_single_slot(state, config, 3)
        assert ok is True

        cmds = [c.args[1].get("c4_cmd", "") for c in mock_send.call_args_list]
        c4_cmds = [c for c in cmds if c]
        # Slot 3 maps to wire 02; every c4_cmd must target that wire only.
        assert c4_cmds
        assert all(
            c.startswith(("c4.dmx.btn 02 ", "c4.dmx.led 02 ")) for c in c4_cmds
        ), c4_cmds
        # The Z2M state write is for button 3 only.
        state_writes = [
            c.args[1] for c in mock_send.call_args_list if "c4_cmd" not in c.args[1]
        ]
        assert len(state_writes) == 1
        assert "button_3_behavior" in state_writes[0]

    @pytest.mark.asyncio
    async def test_unknown_slot_returns_false(self, mock_hass: MagicMock) -> None:
        config = _keypad_config(IEEE_A)
        mgr, _ = _manager_with_device(
            mock_hass, ieee=IEEE_A, friendly_name="Theater", config=config
        )
        state = mgr.devices[IEEE_A]
        with patch.object(mgr, "async_send_mqtt", new_callable=AsyncMock) as mock_send:
            ok = await mgr._push_single_slot(state, config, 99)
        assert ok is False
        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_push_emits_all_slots(self, mock_hass: MagicMock) -> None:
        config = _keypad_config(IEEE_A)
        mgr, _ = _manager_with_device(
            mock_hass, ieee=IEEE_A, friendly_name="Theater", config=config
        )
        state = mgr.devices[IEEE_A]
        with patch.object(mgr, "async_send_mqtt", new_callable=AsyncMock) as mock_send:
            await mgr._push_slot_config(state, config)
        state_writes = [
            c.args[1] for c in mock_send.call_args_list if "c4_cmd" not in c.args[1]
        ]
        # One state write per slot, for all six slots.
        assert len(state_writes) == 6


# ── (d) A publish that raises still records the next-send deadline ────


class TestPublishErrorStillPaces:
    """A raising publish must not swallow the error nor skip the pacing update."""

    @pytest.mark.asyncio
    async def test_raising_publish_still_sets_deadline(
        self, mock_hass: MagicMock
    ) -> None:
        mgr, _ = _manager_with_device(mock_hass, ieee=IEEE_A, friendly_name="Theater")

        boom_msg = "broker down"

        async def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(boom_msg)

        before = time.monotonic()
        with (
            patch(
                "custom_components.control4_dimmers.manager.mqtt.async_publish",
                new=_boom,
            ),
            pytest.raises(RuntimeError, match="broker down"),
        ):
            await mgr.async_send_mqtt(IEEE_A, {"c4_cmd": "c4.dmx.led 00 05 ffffff"})

        # finally ran: a deadline was recorded despite the publish raising, so a
        # later caller is still paced.
        deadline = mgr._next_send_at.get(IEEE_A)
        assert deadline is not None
        assert deadline >= before + MIN_SEND_INTERVAL


# ── (e) FIFO ordering under contention ───────────────────────────────


class TestFifoOrdering:
    """Concurrent sends to one device must complete in submission order."""

    @pytest.mark.asyncio
    async def test_concurrent_sends_preserve_submission_order(
        self, mock_hass: MagicMock
    ) -> None:
        mgr, _ = _manager_with_device(mock_hass, ieee=IEEE_A, friendly_name="Theater")
        rec = _PublishRecorder(hold=0.002)

        async def _send(index: int) -> None:
            await mgr.async_send_mqtt(
                IEEE_A, {"c4_cmd": f"c4.dmx.led 00 05 {index:06d}"}
            )

        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=rec.publish,
        ):
            # Submit in a known order; gather starts them in that order.
            await asyncio.gather(*(_send(i) for i in range(6)))

        colors = [payload["c4_cmd"].split()[-1] for _topic, payload, _t in rec.calls]
        assert colors == [f"{i:06d}" for i in range(6)]


# ── (f) Pruning per-device pacing state on removal ───────────────────


class TestPruneOnRemoval:
    """Device removal must drop the per-device lock and deadline entries."""

    @pytest.mark.asyncio
    async def test_removal_prunes_pacing_maps(self, mock_hass: MagicMock) -> None:
        mgr, _ = _manager_with_device(mock_hass, ieee=IEEE_A, friendly_name="Theater")
        rec = _PublishRecorder(hold=0.0)
        with patch(
            "custom_components.control4_dimmers.manager.mqtt.async_publish",
            new=rec.publish,
        ):
            await mgr.async_send_mqtt(IEEE_A, {"c4_cmd": "c4.dmx.led 00 05 ffffff"})

        # Sending created the send lock and deadline; touch the push lock too.
        mgr._device_lock(mgr._push_locks, IEEE_A)
        assert IEEE_A in mgr._send_locks
        assert IEEE_A in mgr._push_locks
        assert IEEE_A in mgr._next_send_at

        # bridge/devices with an empty list means the device is gone.
        msg = MagicMock()
        msg.topic = "zigbee2mqtt/bridge/devices"
        msg.payload = json.dumps([])
        await mgr._handle_bridge_devices(msg)

        assert IEEE_A not in mgr._devices
        assert IEEE_A not in mgr._send_locks
        assert IEEE_A not in mgr._push_locks
        assert IEEE_A not in mgr._next_send_at

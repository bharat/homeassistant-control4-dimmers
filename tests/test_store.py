"""Tests for Control4Store persistent storage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers.models import DeviceConfig, SlotConfig
from custom_components.control4_dimmers.store import Control4Store

from .conftest import IEEE_DIMMER


class TestControl4Store:
    """Tests for the persistent store."""

    @pytest.fixture
    def store(self) -> Control4Store:
        """Return a store with a mocked HA Store backend."""
        with patch("custom_components.control4_dimmers.store.Store") as mock_cls:
            mock_ha_store = mock_cls.return_value
            mock_ha_store.async_load = AsyncMock(return_value=None)
            mock_ha_store.async_save = AsyncMock()
            s = Control4Store(MagicMock(), "entry1")
        s._store = mock_ha_store
        return s

    @pytest.mark.asyncio
    async def test_load_empty(self, store: Control4Store) -> None:
        store._store.async_load.return_value = None
        await store.async_load()
        assert store.devices == {}

    @pytest.mark.asyncio
    async def test_load_with_data(self, store: Control4Store) -> None:
        store._store.async_load.return_value = {
            "devices": {
                IEEE_DIMMER: {
                    "ieee_address": IEEE_DIMMER,
                    "friendly_name": "Kitchen",
                    "device_type": "dimmer",
                    "slots": [{"slot_id": 1, "name": "Top"}],
                }
            }
        }
        await store.async_load()
        assert IEEE_DIMMER in store.devices
        assert store.devices[IEEE_DIMMER].friendly_name == "Kitchen"
        assert len(store.devices[IEEE_DIMMER].slots) == 1

    @pytest.mark.asyncio
    async def test_load_invalid_data(self, store: Control4Store) -> None:
        store._store.async_load.return_value = "not a dict"
        await store.async_load()
        assert store.devices == {}

    @pytest.mark.asyncio
    async def test_save_device(self, store: Control4Store) -> None:
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
            slots=[SlotConfig(slot_id=1, name="Top")],
        )
        await store.async_save_device(config)
        assert store.get_device(IEEE_DIMMER) is config
        store._store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_save_serializes_all(self, store: Control4Store) -> None:
        store._devices[IEEE_DIMMER] = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
            device_type="dimmer",
        )
        await store.async_save()
        call_args = store._store.async_save.call_args[0][0]
        assert "devices" in call_args
        assert IEEE_DIMMER in call_args["devices"]

    def test_get_device_returns_none(self, store: Control4Store) -> None:
        assert store.get_device("0xNOTHERE") is None

    def test_get_device_returns_config(self, store: Control4Store) -> None:
        config = DeviceConfig(
            ieee_address=IEEE_DIMMER,
            friendly_name="Kitchen",
        )
        store._devices[IEEE_DIMMER] = config
        assert store.get_device(IEEE_DIMMER) is config

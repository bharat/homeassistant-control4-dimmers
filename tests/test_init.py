"""Tests for integration setup and teardown (__init__.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.control4_dimmers import (
    _get_runtime,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.control4_dimmers.const import DOMAIN


class TestGetRuntime:
    """Tests for the _get_runtime helper."""

    def test_returns_none_when_empty(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {}
        assert _get_runtime(mock_hass) is None

    def test_returns_none_when_no_manager(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {DOMAIN: {"entry1": {"other": True}}}
        assert _get_runtime(mock_hass) is None

    def test_returns_runtime(self, mock_hass: MagicMock) -> None:
        runtime = {"manager": MagicMock(), "store": MagicMock()}
        mock_hass.data = {DOMAIN: {"entry1": runtime}}
        assert _get_runtime(mock_hass) is runtime


class TestAsyncSetup:
    """Tests for async_setup (one-time integration setup)."""

    @pytest.mark.asyncio
    async def test_sets_domain_data(self, mock_hass: MagicMock) -> None:
        mock_hass.data = {}
        with (
            patch(
                "custom_components.control4_dimmers._register_websocket_handlers",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.control4_dimmers._register_services",
                new_callable=AsyncMock,
            ),
            patch(
                "custom_components.control4_dimmers._register_frontend",
                new_callable=AsyncMock,
            ),
        ):
            result = await async_setup(mock_hass, {})
        assert result is True
        assert DOMAIN in mock_hass.data


class TestSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_creates_manager_and_store(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_hass.data = {DOMAIN: {}, f"{DOMAIN}_skip_mqtt": True}
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()

        with (
            patch("custom_components.control4_dimmers.Control4Store") as mock_store_cls,
            patch("custom_components.control4_dimmers.Control4Manager") as mock_mgr_cls,
        ):
            mock_store_cls.return_value.async_load = AsyncMock()
            mock_mgr_cls.return_value.async_start = AsyncMock()
            result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True
        entry_data = mock_hass.data[DOMAIN][mock_entry.entry_id]
        assert "manager" in entry_data
        assert "store" in entry_data


class TestUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_cleans_up(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_manager = MagicMock()
        mock_manager.async_stop = AsyncMock()
        mock_hass.data = {
            DOMAIN: {
                mock_entry.entry_id: {
                    "manager": mock_manager,
                    "store": MagicMock(),
                }
            }
        }
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(mock_hass, mock_entry)
        assert result is True
        mock_manager.async_stop.assert_awaited_once()
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_returns_false_on_failure(
        self, mock_hass: MagicMock, mock_entry: MagicMock
    ) -> None:
        mock_hass.data = {DOMAIN: {mock_entry.entry_id: {"manager": MagicMock()}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(mock_hass, mock_entry)
        assert result is False

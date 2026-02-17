"""Tests for frontend registration helpers."""

from __future__ import annotations

from custom_components.control4_dimmers.frontend import JSModuleRegistration


class TestJSModuleRegistration:
    """Tests for utility methods on JSModuleRegistration."""

    def test_get_path_strips_query(self) -> None:
        reg = JSModuleRegistration.__new__(JSModuleRegistration)
        url = "/control4_dimmers/card.js?v=1.0"
        assert reg._get_path(url) == "/control4_dimmers/card.js"
        assert reg._get_path("/control4_dimmers/card.js") == "/control4_dimmers/card.js"

    def test_get_version_extracts_version(self) -> None:
        reg = JSModuleRegistration.__new__(JSModuleRegistration)
        assert reg._get_version("/card.js?v=2.3.1") == "2.3.1"
        assert reg._get_version("/card.js") == "0"
        assert reg._get_version("/card.js?other=1") == "0"

    def test_supports_lovelace_resources_false_when_no_lovelace(self) -> None:
        reg = JSModuleRegistration.__new__(JSModuleRegistration)
        reg.lovelace = None
        assert reg._supports_lovelace_resources() is False

    def test_supports_lovelace_resources_false_when_no_resources_attr(self) -> None:
        reg = JSModuleRegistration.__new__(JSModuleRegistration)

        class FakeLovelace:
            pass

        reg.lovelace = FakeLovelace()
        assert reg._supports_lovelace_resources() is False

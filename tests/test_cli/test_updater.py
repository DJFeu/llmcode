"""Tests for self-update utilities."""
from __future__ import annotations

from llm_code.cli.updater import _get_installed_version, _parse_version


class TestParseVersion:
    def test_normal(self) -> None:
        assert _parse_version("1.18.0") == (1, 18, 0)

    def test_two_part(self) -> None:
        assert _parse_version("1.18") == (1, 18)

    def test_invalid(self) -> None:
        assert _parse_version("abc") == (0, 0, 0)

    def test_empty(self) -> None:
        assert _parse_version("") == (0, 0, 0)

    def test_comparison(self) -> None:
        assert _parse_version("1.18.0") > _parse_version("1.17.0")
        assert _parse_version("2.0.0") > _parse_version("1.99.99")
        assert _parse_version("1.18.0") == _parse_version("1.18.0")


class TestGetInstalledVersion:
    def test_returns_string(self) -> None:
        v = _get_installed_version()
        assert isinstance(v, str)
        # Should be a version string or "0.0.0" fallback
        parts = v.split(".")
        assert len(parts) >= 2

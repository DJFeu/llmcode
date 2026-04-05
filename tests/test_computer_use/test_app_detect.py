"""Tests for app detection on macOS."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_code.computer_use.app_detect import AppInfo, get_frontmost_app_sync


class TestAppInfo:
    def test_create(self) -> None:
        info = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1234)
        assert info.name == "Safari"
        assert info.bundle_id == "com.apple.Safari"
        assert info.pid == 1234

    def test_frozen(self) -> None:
        info = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1)
        with pytest.raises(AttributeError):
            info.name = "x"


class TestGetFrontmostApp:
    @patch("llm_code.computer_use.app_detect._get_via_osascript")
    def test_osascript_fallback(self, mock_osa) -> None:
        mock_osa.return_value = AppInfo(name="Finder", bundle_id="com.apple.finder", pid=100)
        result = get_frontmost_app_sync()
        assert result.name == "Finder"

    @patch("llm_code.computer_use.app_detect._get_via_osascript", side_effect=RuntimeError("no osa"))
    def test_fallback_on_error(self, _mock) -> None:
        result = get_frontmost_app_sync()
        assert result.name == "Unknown"
        assert result.bundle_id == ""
        assert result.pid == 0

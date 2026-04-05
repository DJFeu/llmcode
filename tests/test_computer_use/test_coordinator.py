"""Tests for ComputerUseCoordinator."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_code.computer_use.app_detect import AppInfo


class TestCoordinator:
    @patch("llm_code.computer_use.coordinator.get_frontmost_app_sync")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_click_and_screenshot(self, mock_click, mock_ss, mock_app):
        mock_ss.return_value = "BASE64IMG"
        mock_app.return_value = AppInfo(name="Notes", bundle_id="com.apple.Notes", pid=1)
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        coord = ComputerUseCoordinator(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = coord.click_and_observe(100, 200)
        mock_click.assert_called_once_with(100, 200, button="left")
        mock_ss.assert_called_once()
        assert result["screenshot_base64"] == "BASE64IMG"

    @patch("llm_code.computer_use.coordinator.get_frontmost_app_sync")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.keyboard_type")
    def test_type_and_screenshot(self, mock_type, mock_ss, mock_app):
        mock_ss.return_value = "BASE64IMG"
        mock_app.return_value = AppInfo(name="Notes", bundle_id="com.apple.Notes", pid=1)
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        coord = ComputerUseCoordinator(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = coord.type_and_observe("hello")
        mock_type.assert_called_once_with("hello")
        assert result["screenshot_base64"] == "BASE64IMG"

    def test_disabled_raises(self):
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        coord = ComputerUseCoordinator(ComputerUseConfig(enabled=False))
        with pytest.raises(RuntimeError, match="not enabled"):
            coord.screenshot()

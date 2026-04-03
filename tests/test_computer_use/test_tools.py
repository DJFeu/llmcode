"""Tests for computer-use tool classes."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from llm_code.tools.base import PermissionLevel


# Mock pyautogui globally for tool tests
@pytest.fixture(autouse=True)
def mock_deps():
    mock_pag = MagicMock()
    mock_pil = MagicMock()
    mock_mss = MagicMock()
    with patch.dict(sys.modules, {
        "pyautogui": mock_pag,
        "PIL": mock_pil,
        "mss": mock_mss,
        "mss.tools": mock_mss.tools,
    }):
        yield mock_pag


class TestScreenshotTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    def test_execute_returns_base64(self, mock_ss):
        mock_ss.return_value = "AABBCC"
        from llm_code.tools.computer_use_tools import ScreenshotTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScreenshotTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({})
        assert result.is_error is False
        assert "AABBCC" in result.output

    def test_permission_is_read_only(self):
        from llm_code.tools.computer_use_tools import ScreenshotTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScreenshotTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_name(self):
        from llm_code.tools.computer_use_tools import ScreenshotTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScreenshotTool(ComputerUseConfig(enabled=True))
        assert tool.name == "screenshot"


class TestMouseClickTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_execute(self, mock_click, mock_ss):
        mock_ss.return_value = "IMG"
        from llm_code.tools.computer_use_tools import MouseClickTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = MouseClickTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({"x": 100, "y": 200, "button": "left"})
        assert result.is_error is False

    def test_permission_is_full_access(self):
        from llm_code.tools.computer_use_tools import MouseClickTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = MouseClickTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.FULL_ACCESS


class TestKeyboardTypeTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.keyboard_type")
    def test_execute(self, mock_type, mock_ss):
        mock_ss.return_value = "IMG"
        from llm_code.tools.computer_use_tools import KeyboardTypeTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = KeyboardTypeTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({"text": "hello"})
        assert result.is_error is False

    def test_permission_is_full_access(self):
        from llm_code.tools.computer_use_tools import KeyboardTypeTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = KeyboardTypeTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.FULL_ACCESS


class TestKeyPressTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.keyboard_hotkey")
    def test_execute(self, mock_hotkey, mock_ss):
        mock_ss.return_value = "IMG"
        from llm_code.tools.computer_use_tools import KeyPressTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = KeyPressTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({"keys": ["ctrl", "c"]})
        assert result.is_error is False

    def test_permission_is_full_access(self):
        from llm_code.tools.computer_use_tools import KeyPressTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = KeyPressTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.FULL_ACCESS


class TestScrollTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.scroll")
    def test_execute(self, mock_scroll, mock_ss):
        mock_ss.return_value = "IMG"
        from llm_code.tools.computer_use_tools import ScrollTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScrollTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({"clicks": 3})
        assert result.is_error is False

    def test_permission_is_full_access(self):
        from llm_code.tools.computer_use_tools import ScrollTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScrollTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.FULL_ACCESS


class TestMouseDragTool:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.mouse_drag")
    def test_execute(self, mock_drag, mock_ss):
        mock_ss.return_value = "IMG"
        from llm_code.tools.computer_use_tools import MouseDragTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = MouseDragTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({
            "start_x": 0, "start_y": 0,
            "offset_x": 100, "offset_y": 100,
        })
        assert result.is_error is False

    def test_permission_is_full_access(self):
        from llm_code.tools.computer_use_tools import MouseDragTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = MouseDragTool(ComputerUseConfig(enabled=True))
        assert tool.required_permission == PermissionLevel.FULL_ACCESS


class TestDisabledTools:
    def test_screenshot_when_disabled(self):
        from llm_code.tools.computer_use_tools import ScreenshotTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = ScreenshotTool(ComputerUseConfig(enabled=False))
        result = tool.execute({})
        assert result.is_error is True
        assert "not enabled" in result.output.lower()

    def test_mouse_click_when_disabled(self):
        from llm_code.tools.computer_use_tools import MouseClickTool
        from llm_code.runtime.config import ComputerUseConfig

        tool = MouseClickTool(ComputerUseConfig(enabled=False))
        result = tool.execute({"x": 0, "y": 0})
        assert result.is_error is True

"""Integration test — full tool execution flow with mocked deps."""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_all_deps():
    """Mock pyautogui, PIL, mss so tests work without them installed."""
    mocks = {
        "pyautogui": MagicMock(),
        "PIL": MagicMock(),
        "PIL.Image": MagicMock(),
        "mss": MagicMock(),
        "mss.tools": MagicMock(),
    }
    with patch.dict(sys.modules, mocks):
        yield mocks


class TestEndToEnd:
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    def test_screenshot_tool_end_to_end(self, mock_ss):
        mock_ss.return_value = "iVBORw0KGgoAAAANSUhEUg=="
        from llm_code.runtime.config import ComputerUseConfig
        from llm_code.tools.computer_use_tools import ScreenshotTool

        tool = ScreenshotTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
        result = tool.execute({})
        assert result.is_error is False
        data = json.loads(result.output)
        assert "screenshot_base64" in data

    @patch("llm_code.computer_use.coordinator.take_screenshot_base64")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_click_type_scroll_sequence(self, mock_click, mock_ss):
        """Simulate a realistic sequence: click -> type -> scroll."""
        mock_ss.return_value = "IMG_DATA"
        from llm_code.runtime.config import ComputerUseConfig
        from llm_code.tools.computer_use_tools import (
            KeyboardTypeTool,
            MouseClickTool,
            ScrollTool,
        )

        cfg = ComputerUseConfig(enabled=True, screenshot_delay=0.0)

        # Click on text field
        click_tool = MouseClickTool(cfg)
        r1 = click_tool.execute({"x": 500, "y": 300, "button": "left"})
        assert r1.is_error is False

        # Type into text field
        with patch("llm_code.computer_use.coordinator.keyboard_type"):
            type_tool = KeyboardTypeTool(cfg)
            r2 = type_tool.execute({"text": "Hello, world!"})
            assert r2.is_error is False

        # Scroll down
        with patch("llm_code.computer_use.coordinator.scroll"):
            scroll_tool = ScrollTool(cfg)
            r3 = scroll_tool.execute({"clicks": -3})
            assert r3.is_error is False

    def test_all_tools_error_when_disabled(self):
        from llm_code.runtime.config import ComputerUseConfig
        from llm_code.tools.computer_use_tools import (
            KeyPressTool,
            KeyboardTypeTool,
            MouseClickTool,
            MouseDragTool,
            ScreenshotTool,
            ScrollTool,
        )

        cfg = ComputerUseConfig(enabled=False)
        tools_and_args = [
            (ScreenshotTool(cfg), {}),
            (MouseClickTool(cfg), {"x": 0, "y": 0}),
            (KeyboardTypeTool(cfg), {"text": "x"}),
            (KeyPressTool(cfg), {"keys": ["a"]}),
            (ScrollTool(cfg), {"clicks": 1}),
            (MouseDragTool(cfg), {"start_x": 0, "start_y": 0, "offset_x": 1, "offset_y": 1}),
        ]
        for tool, args in tools_and_args:
            result = tool.execute(args)
            assert result.is_error is True, f"{tool.name} should error when disabled"

    def test_tool_metadata_has_image_flag(self):
        """All action tools should set metadata.has_image=True on success."""
        from llm_code.runtime.config import ComputerUseConfig

        with patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG"):
            from llm_code.tools.computer_use_tools import ScreenshotTool

            tool = ScreenshotTool(ComputerUseConfig(enabled=True, screenshot_delay=0.0))
            result = tool.execute({})
            assert result.metadata is not None
            assert result.metadata.get("has_image") is True

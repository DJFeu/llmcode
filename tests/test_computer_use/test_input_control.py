"""Tests for input control — pyautogui fully mocked."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Create a mock pyautogui so tests run without it installed
mock_pyautogui = MagicMock()


@pytest.fixture(autouse=True)
def patch_pyautogui():
    with patch.dict(sys.modules, {"pyautogui": mock_pyautogui}):
        mock_pyautogui.reset_mock()
        yield


class TestMouseMove:
    def test_moves_to_coordinates(self):
        from llm_code.computer_use.input_control import mouse_move
        mouse_move(100, 200)
        mock_pyautogui.moveTo.assert_called_once_with(100, 200, duration=0.05)


class TestMouseClick:
    def test_left_click_at_position(self):
        from llm_code.computer_use.input_control import mouse_click
        mouse_click(300, 400)
        mock_pyautogui.click.assert_called_once_with(300, 400, button="left")

    def test_right_click(self):
        from llm_code.computer_use.input_control import mouse_click
        mouse_click(300, 400, button="right")
        mock_pyautogui.click.assert_called_once_with(300, 400, button="right")


class TestMouseDoubleClick:
    def test_double_click(self):
        from llm_code.computer_use.input_control import mouse_double_click
        mouse_double_click(500, 600)
        mock_pyautogui.doubleClick.assert_called_once_with(500, 600)


class TestMouseDrag:
    def test_drag_from_to(self):
        from llm_code.computer_use.input_control import mouse_drag
        mouse_drag(0, 0, 100, 100, duration=0.5)
        mock_pyautogui.moveTo.assert_called_once_with(0, 0, duration=0.05)
        mock_pyautogui.drag.assert_called_once_with(100, 100, duration=0.5, button="left")


class TestKeyboardType:
    def test_types_text(self):
        from llm_code.computer_use.input_control import keyboard_type
        keyboard_type("hello world")
        mock_pyautogui.typewrite.assert_called_once_with("hello world", interval=0.05)


class TestKeyboardHotkey:
    def test_hotkey_combo(self):
        from llm_code.computer_use.input_control import keyboard_hotkey
        keyboard_hotkey("ctrl", "c")
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")


class TestScroll:
    def test_scroll_up(self):
        from llm_code.computer_use.input_control import scroll
        scroll(3)
        mock_pyautogui.scroll.assert_called_once_with(3)

    def test_scroll_down(self):
        from llm_code.computer_use.input_control import scroll
        scroll(-3)
        mock_pyautogui.scroll.assert_called_once_with(-3)

    def test_scroll_at_position(self):
        from llm_code.computer_use.input_control import scroll
        scroll(5, x=100, y=200)
        mock_pyautogui.scroll.assert_called_once_with(5, x=100, y=200)


class TestImportGuard:
    def test_raises_when_pyautogui_missing(self):
        with patch.dict(sys.modules, {"pyautogui": None}):
            # Force reimport
            import importlib
            from llm_code.computer_use import input_control
            importlib.reload(input_control)
            with pytest.raises(RuntimeError, match="pyautogui"):
                input_control.mouse_move(0, 0)

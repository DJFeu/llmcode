"""Tool classes for computer use (GUI automation)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from llm_code.tools.base import PermissionLevel, Tool, ToolResult

if TYPE_CHECKING:
    from llm_code.runtime.config import ComputerUseConfig


class _ComputerUseTool(Tool):
    """Base class for computer-use tools with shared config check."""

    def __init__(self, config: "ComputerUseConfig") -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        self._config = config
        self._coordinator = ComputerUseCoordinator(config)

    def _check_enabled(self) -> ToolResult | None:
        if not self._config.enabled:
            return ToolResult(
                output="Computer use is not enabled. Set computer_use.enabled=true in config.",
                is_error=True,
            )
        return None


class ScreenshotTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "screenshot"

    @property
    def description(self) -> str:
        return "Take a screenshot of the current screen. Returns a base64-encoded PNG image."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.screenshot()
            return ToolResult(
                output=json.dumps(result),
                metadata={"has_image": True},
            )
        except Exception as exc:
            return ToolResult(output=f"Screenshot failed: {exc}", is_error=True)


class MouseClickTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "mouse_click"

    @property
    def description(self) -> str:
        return "Click the mouse at (x, y) coordinates. Returns a screenshot after clicking."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "Mouse button",
                },
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.click_and_observe(
                x=args["x"],
                y=args["y"],
                button=args.get("button", "left"),
            )
            return ToolResult(output=json.dumps(result), metadata={"has_image": True})
        except Exception as exc:
            return ToolResult(output=f"Mouse click failed: {exc}", is_error=True)


class KeyboardTypeTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "keyboard_type"

    @property
    def description(self) -> str:
        return "Type text using the keyboard. Returns a screenshot after typing."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.type_and_observe(args["text"])
            return ToolResult(output=json.dumps(result), metadata={"has_image": True})
        except Exception as exc:
            return ToolResult(output=f"Keyboard type failed: {exc}", is_error=True)


class KeyPressTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "key_press"

    @property
    def description(self) -> str:
        return "Press a keyboard shortcut (e.g., ctrl+c). Returns a screenshot after pressing."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keys to press simultaneously (e.g., ['ctrl', 'c'])",
                },
            },
            "required": ["keys"],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.hotkey_and_observe(*args["keys"])
            return ToolResult(output=json.dumps(result), metadata={"has_image": True})
        except Exception as exc:
            return ToolResult(output=f"Key press failed: {exc}", is_error=True)


class ScrollTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "scroll"

    @property
    def description(self) -> str:
        return "Scroll the mouse wheel. Positive clicks = up, negative = down. Returns a screenshot."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "clicks": {
                    "type": "integer",
                    "description": "Scroll amount (positive=up, negative=down)",
                },
                "x": {"type": "integer", "description": "Optional X position"},
                "y": {"type": "integer", "description": "Optional Y position"},
            },
            "required": ["clicks"],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.scroll_and_observe(
                clicks=args["clicks"],
                x=args.get("x"),
                y=args.get("y"),
            )
            return ToolResult(output=json.dumps(result), metadata={"has_image": True})
        except Exception as exc:
            return ToolResult(output=f"Scroll failed: {exc}", is_error=True)


class MouseDragTool(_ComputerUseTool):
    @property
    def name(self) -> str:
        return "mouse_drag"

    @property
    def description(self) -> str:
        return "Drag the mouse from a start position by an offset. Returns a screenshot."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer", "description": "Start X coordinate"},
                "start_y": {"type": "integer", "description": "Start Y coordinate"},
                "offset_x": {"type": "integer", "description": "Horizontal drag distance"},
                "offset_y": {"type": "integer", "description": "Vertical drag distance"},
                "duration": {
                    "type": "number",
                    "default": 0.5,
                    "description": "Drag duration in seconds",
                },
            },
            "required": ["start_x", "start_y", "offset_x", "offset_y"],
            "additionalProperties": False,
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    def execute(self, args: dict) -> ToolResult:
        err = self._check_enabled()
        if err:
            return err
        try:
            result = self._coordinator.drag_and_observe(
                start_x=args["start_x"],
                start_y=args["start_y"],
                offset_x=args["offset_x"],
                offset_y=args["offset_y"],
                duration=args.get("duration", 0.5),
            )
            return ToolResult(output=json.dumps(result), metadata={"has_image": True})
        except Exception as exc:
            return ToolResult(output=f"Mouse drag failed: {exc}", is_error=True)

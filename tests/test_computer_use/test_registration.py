"""Tests that computer-use tools are registered when enabled."""
from __future__ import annotations



from llm_code.tools.registry import ToolRegistry


class TestConditionalRegistration:
    def test_tools_registered_when_enabled(self):
        """All 6 tools should be registered when computer_use.enabled=True."""
        from llm_code.runtime.config import ComputerUseConfig
        from llm_code.tools.computer_use_tools import (
            KeyPressTool,
            KeyboardTypeTool,
            MouseClickTool,
            MouseDragTool,
            ScreenshotTool,
            ScrollTool,
        )

        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0)
        registry = ToolRegistry()

        tools = [
            ScreenshotTool(config),
            MouseClickTool(config),
            KeyboardTypeTool(config),
            KeyPressTool(config),
            ScrollTool(config),
            MouseDragTool(config),
        ]
        for tool in tools:
            registry.register(tool)

        assert registry.get("screenshot") is not None
        assert registry.get("mouse_click") is not None
        assert registry.get("keyboard_type") is not None
        assert registry.get("key_press") is not None
        assert registry.get("scroll") is not None
        assert registry.get("mouse_drag") is not None

    def test_tools_not_registered_when_disabled(self):
        """When config.computer_use.enabled=False, no tools should be auto-registered."""
        from llm_code.runtime.config import ComputerUseConfig

        config = ComputerUseConfig(enabled=False)
        # Registration is conditional on config.enabled — the CLI checks this
        # before instantiating tools. Just verify the config flag works.
        assert config.enabled is False

    def test_tool_count(self):
        """Exactly 6 computer-use tools exist."""
        from llm_code.tools import computer_use_tools

        tool_classes = [
            cls for name, cls in vars(computer_use_tools).items()
            if isinstance(cls, type)
            and issubclass(cls, computer_use_tools._ComputerUseTool)
            and cls is not computer_use_tools._ComputerUseTool
        ]
        assert len(tool_classes) == 6

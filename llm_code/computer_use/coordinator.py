"""Coordinator that composes screenshot + input for tool actions."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.config import ComputerUseConfig

from llm_code.computer_use.app_detect import get_frontmost_app_sync
from llm_code.computer_use.app_tier import (
    DEFAULT_RULES,
    TIER_PERMISSIONS,
    AppTierClassifier,
    AppTierDenied,
    AppTierRule,
)
from llm_code.computer_use.input_control import (
    keyboard_hotkey,
    keyboard_type,
    mouse_click,
    mouse_drag,
    scroll,
)
from llm_code.computer_use.screenshot import take_screenshot_base64


class ComputerUseCoordinator:
    """Orchestrates GUI actions with follow-up screenshots and app-aware tier enforcement."""

    def __init__(self, config: "ComputerUseConfig") -> None:
        self._config = config
        user_rules = tuple(
            AppTierRule(pattern=r["pattern"], tier=r["tier"])
            for r in self._config.app_tiers
            if isinstance(r, dict) and "pattern" in r and "tier" in r
        )
        self._classifier = AppTierClassifier(rules=user_rules + DEFAULT_RULES)

    def _ensure_enabled(self) -> None:
        if not self._config.enabled:
            raise RuntimeError("Computer use is not enabled. Set computer_use.enabled=true in config.")

    def _check_tier(self, action: str) -> None:
        app = get_frontmost_app_sync()
        tier = self._classifier.classify(app)
        if action not in TIER_PERMISSIONS[tier]:
            hint = ""
            if tier == "read":
                hint = "Use MCP browser tools (chrome-devtools) instead."
            elif tier == "click" and action in ("type", "key", "hotkey"):
                hint = "Use the Bash tool instead for terminal input."
            raise AppTierDenied(app=app.name, tier=tier, action=action, hint=hint)

    def _delay_then_screenshot(self) -> str:
        if self._config.screenshot_delay > 0:
            time.sleep(self._config.screenshot_delay)
        return take_screenshot_base64()

    def screenshot(self) -> dict:
        self._ensure_enabled()
        self._check_tier("screenshot")
        img = self._delay_then_screenshot()
        return {"screenshot_base64": img}

    def click_and_observe(self, x: int, y: int, button: str = "left") -> dict:
        self._ensure_enabled()
        self._check_tier("left_click")
        mouse_click(x, y, button=button)
        img = self._delay_then_screenshot()
        return {"action": "click", "x": x, "y": y, "button": button, "screenshot_base64": img}

    def type_and_observe(self, text: str) -> dict:
        self._ensure_enabled()
        self._check_tier("type")
        keyboard_type(text)
        img = self._delay_then_screenshot()
        return {"action": "type", "text": text, "screenshot_base64": img}

    def hotkey_and_observe(self, *keys: str) -> dict:
        self._ensure_enabled()
        self._check_tier("hotkey")
        keyboard_hotkey(*keys)
        img = self._delay_then_screenshot()
        return {"action": "hotkey", "keys": list(keys), "screenshot_base64": img}

    def scroll_and_observe(self, clicks: int, x: int | None = None, y: int | None = None) -> dict:
        self._ensure_enabled()
        self._check_tier("scroll")
        scroll(clicks, x=x, y=y)
        img = self._delay_then_screenshot()
        return {"action": "scroll", "clicks": clicks, "screenshot_base64": img}

    def drag_and_observe(self, start_x: int, start_y: int, offset_x: int, offset_y: int, duration: float = 0.5) -> dict:
        self._ensure_enabled()
        self._check_tier("drag")
        mouse_drag(start_x, start_y, offset_x, offset_y, duration=duration)
        img = self._delay_then_screenshot()
        return {"action": "drag", "start_x": start_x, "start_y": start_y, "offset_x": offset_x, "offset_y": offset_y, "screenshot_base64": img}

"""App-aware tier classification and permission enforcement for computer use."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from llm_code.computer_use.app_detect import AppInfo


@dataclass(frozen=True)
class AppTierRule:
    """Maps a bundle_id glob pattern to a tier."""
    pattern: str
    tier: str


DEFAULT_RULES: tuple[AppTierRule, ...] = (
    AppTierRule("com.apple.Safari*", "read"),
    AppTierRule("com.google.Chrome*", "read"),
    AppTierRule("org.mozilla.firefox*", "read"),
    AppTierRule("company.thebrowser.Browser*", "read"),
    AppTierRule("com.microsoft.edgemac*", "read"),
    AppTierRule("com.apple.Terminal*", "click"),
    AppTierRule("com.googlecode.iterm2*", "click"),
    AppTierRule("com.microsoft.VSCode*", "click"),
    AppTierRule("com.jetbrains.*", "click"),
)


TIER_PERMISSIONS: dict[str, frozenset[str]] = {
    "read": frozenset({"screenshot", "get_frontmost_app"}),
    "click": frozenset({"screenshot", "get_frontmost_app", "left_click", "scroll"}),
    "full": frozenset({
        "screenshot", "get_frontmost_app", "left_click", "right_click",
        "double_click", "drag", "scroll", "type", "key", "hotkey",
    }),
}


class AppTierDenied(Exception):
    def __init__(self, app: str, tier: str, action: str, hint: str = "") -> None:
        self.app = app
        self.tier = tier
        self.action = action
        self.hint = hint
        super().__init__(f"Action '{action}' denied for app '{app}' (tier='{tier}'). {hint}")


@dataclass(frozen=True)
class AppTierClassifier:
    rules: tuple[AppTierRule, ...]

    def classify(self, app: AppInfo) -> str:
        for rule in self.rules:
            if fnmatch.fnmatch(app.bundle_id, rule.pattern):
                return rule.tier
        return "full"

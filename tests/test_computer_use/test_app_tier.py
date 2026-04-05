"""Tests for app tier classification and enforcement."""
from __future__ import annotations

import pytest

from llm_code.computer_use.app_detect import AppInfo
from llm_code.computer_use.app_tier import (
    DEFAULT_RULES,
    TIER_PERMISSIONS,
    AppTierClassifier,
    AppTierDenied,
    AppTierRule,
)


class TestAppTierRule:
    def test_create(self) -> None:
        rule = AppTierRule(pattern="com.google.Chrome*", tier="read")
        assert rule.tier == "read"

    def test_frozen(self) -> None:
        rule = AppTierRule(pattern="x", tier="full")
        with pytest.raises(AttributeError):
            rule.tier = "read"


class TestAppTierClassifier:
    def test_chrome_is_read(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Chrome", bundle_id="com.google.Chrome", pid=1)
        assert classifier.classify(app) == "read"

    def test_safari_is_read(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1)
        assert classifier.classify(app) == "read"

    def test_terminal_is_click(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Terminal", bundle_id="com.apple.Terminal", pid=1)
        assert classifier.classify(app) == "click"

    def test_vscode_is_click(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="VS Code", bundle_id="com.microsoft.VSCode", pid=1)
        assert classifier.classify(app) == "click"

    def test_unknown_app_is_full(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Notes", bundle_id="com.apple.Notes", pid=1)
        assert classifier.classify(app) == "full"

    def test_user_rules_override(self) -> None:
        user_rule = AppTierRule(pattern="com.slack.*", tier="click")
        classifier = AppTierClassifier(rules=(user_rule,) + DEFAULT_RULES)
        app = AppInfo(name="Slack", bundle_id="com.slack.Slack", pid=1)
        assert classifier.classify(app) == "click"

    def test_empty_rules_defaults_full(self) -> None:
        classifier = AppTierClassifier(rules=())
        app = AppInfo(name="X", bundle_id="x.y.z", pid=1)
        assert classifier.classify(app) == "full"


class TestTierPermissions:
    def test_read_allows_screenshot(self) -> None:
        assert "screenshot" in TIER_PERMISSIONS["read"]

    def test_read_blocks_click(self) -> None:
        assert "left_click" not in TIER_PERMISSIONS["read"]

    def test_click_allows_left_click(self) -> None:
        assert "left_click" in TIER_PERMISSIONS["click"]

    def test_click_blocks_type(self) -> None:
        assert "type" not in TIER_PERMISSIONS["click"]

    def test_full_allows_all(self) -> None:
        assert "type" in TIER_PERMISSIONS["full"]
        assert "left_click" in TIER_PERMISSIONS["full"]
        assert "hotkey" in TIER_PERMISSIONS["full"]


class TestAppTierDenied:
    def test_message(self) -> None:
        err = AppTierDenied(app="Chrome", tier="read", action="left_click", hint="Use browser MCP")
        assert "Chrome" in str(err)
        assert "read" in str(err)
        assert "left_click" in str(err)

    def test_hint(self) -> None:
        err = AppTierDenied(app="Chrome", tier="read", action="type", hint="Use browser MCP")
        assert err.hint == "Use browser MCP"

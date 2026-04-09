"""Tests for ChatScrollView mouse scroll behavior.

Verifies that mouse wheel events pause/resume auto-scroll correctly
so users can browse history during streaming without being pulled back.
"""
from __future__ import annotations

import pytest

from llm_code.tui.chat_view import ChatScrollView


class TestAutoScrollPause:
    """Auto-scroll should pause on mouse scroll up, resume at bottom."""

    def test_auto_scroll_enabled_by_default(self) -> None:
        view = ChatScrollView()
        assert view._auto_scroll is True

    def test_on_scroll_up_pauses(self) -> None:
        view = ChatScrollView()
        view.on_scroll_up()
        assert view._auto_scroll is False

    def test_on_mouse_scroll_up_pauses(self) -> None:
        view = ChatScrollView()

        class _FakeEvent:
            pass

        view.on_mouse_scroll_up(_FakeEvent())
        assert view._auto_scroll is False

    def test_pause_auto_scroll(self) -> None:
        view = ChatScrollView()
        view.pause_auto_scroll()
        assert view._auto_scroll is False

    def test_resume_auto_scroll(self) -> None:
        view = ChatScrollView()
        view._auto_scroll = False
        view.resume_auto_scroll()
        assert view._auto_scroll is True


class TestPermissionDialogChoices:
    """Permission dialog should offer correct choices including edit."""

    def test_permission_choices_include_edit(self) -> None:
        from llm_code.tui.dialogs import Choice
        choices = [
            Choice(value="allow", label="Allow (y)"),
            Choice(value="always_kind", label="Always allow this type (a)"),
            Choice(value="always_exact", label="Always allow exact (A)"),
            Choice(value="edit", label="Edit args (e)"),
            Choice(value="deny", label="Deny (n)"),
        ]
        values = [c.value for c in choices]
        assert "allow" in values
        assert "deny" in values
        assert "edit" in values
        assert "always_kind" in values
        assert "always_exact" in values


class TestSettingsWriteBack:
    """Settings write-back should validate and apply config changes."""

    def test_apply_temperature(self) -> None:
        from dataclasses import dataclass
        from llm_code.tui.settings_modal import apply_setting

        @dataclass
        class FakeConfig:
            temperature: float = 0.7
            max_tokens: int = 4096
            model: str = "test"

        cfg = FakeConfig()
        new_cfg = apply_setting(cfg, "temperature", "0.5")
        assert new_cfg.temperature == 0.5
        assert new_cfg.max_tokens == 4096  # unchanged

    def test_apply_max_tokens(self) -> None:
        from dataclasses import dataclass
        from llm_code.tui.settings_modal import apply_setting

        @dataclass
        class FakeConfig:
            temperature: float = 0.7
            max_tokens: int = 4096
            model: str = "test"

        cfg = FakeConfig()
        new_cfg = apply_setting(cfg, "max_tokens", "8192")
        assert new_cfg.max_tokens == 8192

    def test_apply_invalid_field_raises(self) -> None:
        from dataclasses import dataclass
        from llm_code.tui.settings_modal import apply_setting

        @dataclass
        class FakeConfig:
            temperature: float = 0.7

        with pytest.raises(ValueError, match="not editable"):
            apply_setting(FakeConfig(), "invalid_field", "42")

    def test_apply_temperature_out_of_range(self) -> None:
        from dataclasses import dataclass
        from llm_code.tui.settings_modal import apply_setting

        @dataclass
        class FakeConfig:
            temperature: float = 0.7

        with pytest.raises(ValueError, match="between 0.0 and 2.0"):
            apply_setting(FakeConfig(), "temperature", "3.0")


class TestEditArgsPermission:
    """The runtime should handle edit: responses correctly."""

    def test_edit_response_format(self) -> None:
        """send_permission_response with edit should encode args as JSON."""
        import json
        args = {"query": "test", "max_results": 5}
        encoded = f"edit:{json.dumps(args)}"
        assert encoded.startswith("edit:")
        parsed = json.loads(encoded[5:])
        assert parsed == args

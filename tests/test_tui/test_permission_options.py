"""Tests for the multi-option permission UX and in-runtime allowlist."""
from __future__ import annotations


import pytest

from llm_code.tui.chat_widgets import PermissionInline


@pytest.mark.unit
class TestPermissionInlineWidget:
    def test_renders_four_core_options(self) -> None:
        w = PermissionInline("bash", '{"command": "git status"}')
        text = w.render()
        plain = text.plain
        assert "[y]" in plain
        assert "[a]" in plain
        assert "[A]" in plain
        assert "[n]" in plain

    def test_bash_always_label_includes_prefix(self) -> None:
        w = PermissionInline("bash", '{"command": "git status"}')
        plain = w.render().plain
        assert "git" in plain

    def test_edit_file_offers_workspace_label(self) -> None:
        w = PermissionInline("edit_file", '{"path": "/x/y.py"}')
        plain = w.render().plain
        assert "workspace" in plain

    def test_edit_args_option_for_supported_tools(self) -> None:
        w = PermissionInline("bash", '{"command": "ls"}')
        assert "[e]" in w.render().plain

    def test_no_edit_args_for_unsupported_tools(self) -> None:
        w = PermissionInline("read_file", '{"path": "/x"}')
        assert "[e]" not in w.render().plain


@pytest.mark.unit
class TestSessionAllowlist:
    """Smoke test for the in-runtime session allowlist API."""

    def _make_runtime_stub(self):
        # Build a minimal stub that exercises the helper methods without
        # constructing the full Conversation object (which has heavy deps).
        from llm_code.runtime.conversation import ConversationRuntime
        from llm_code.runtime.permission_manager import PermissionManager
        from unittest.mock import MagicMock

        rt = ConversationRuntime.__new__(ConversationRuntime)
        class _Ctx:
            cwd = "/work/proj"
        rt._context = _Ctx()
        rt._perm_mgr = PermissionManager(MagicMock(), MagicMock(), context=_Ctx())
        return rt

    def test_record_always_kind_for_bash_adds_prefix(self) -> None:
        rt = self._make_runtime_stub()
        rt.record_permission_choice(
            "always_kind", "bash", '{"command": "git status"}',
            {"command": "git status"},
        )
        assert "bash" in rt._session_allowed_tools
        assert "git " in rt._session_allowed_prefixes

    def test_is_session_allowed_by_prefix(self) -> None:
        rt = self._make_runtime_stub()
        rt._session_allowed_prefixes.add("git ")
        assert rt.is_session_allowed("bash", "any", {"command": "git diff"})
        assert not rt.is_session_allowed("bash", "any", {"command": "rm -rf /"})

    def test_record_always_exact(self) -> None:
        rt = self._make_runtime_stub()
        rt.record_permission_choice(
            "always_exact", "bash", '{"command": "ls"}', {"command": "ls"},
        )
        assert ("bash", '{"command": "ls"}') in rt._session_allowed_exact
        assert rt.is_session_allowed("bash", '{"command": "ls"}', {"command": "ls"})

    def test_edit_file_workspace_root(self) -> None:
        rt = self._make_runtime_stub()
        rt.record_permission_choice(
            "always_kind", "edit_file", '{"path": "/work/proj/a.py"}',
            {"path": "/work/proj/a.py"},
        )
        assert "/work/proj" in rt._session_allowed_path_roots
        assert rt.is_session_allowed(
            "edit_file", "any", {"path": "/work/proj/sub/b.py"},
        )
        assert not rt.is_session_allowed(
            "edit_file", "any", {"path": "/other/c.py"},
        )

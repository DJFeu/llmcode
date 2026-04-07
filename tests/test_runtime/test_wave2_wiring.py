"""Wave 2 wiring tests: builtin hooks, prompt sections, keyword actions, /orchestrate."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from llm_code.runtime import builtin_hooks
from llm_code.runtime.builtin_hooks import register_named
from llm_code.runtime.hooks import HookRunner


# ---------------------------------------------------------------------------
# Task 1 — builtin hooks wiring
# ---------------------------------------------------------------------------


class _RecordingRunner:
    """Stand-in HookRunner that records subscribe-style calls."""

    def __init__(self) -> None:
        self.subscribed: list[tuple[str, Any]] = []

    def subscribe(self, event: str, handler: Any) -> None:
        self.subscribed.append((event, handler))

    # Some hooks might call .on(...) — keep an alias
    def on(self, event: str, handler: Any) -> None:
        self.subscribe(event, handler)


def test_register_named_warns_on_unknown(caplog: pytest.LogCaptureFixture) -> None:
    runner = HookRunner()
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.builtin_hooks"):
        registered = register_named(runner, ("auto_format", "does_not_exist"))
    assert "auto_format" in registered
    assert "does_not_exist" not in registered
    assert any("does_not_exist" in rec.message for rec in caplog.records)


def test_register_named_empty_is_noop() -> None:
    runner = HookRunner()
    assert register_named(runner, ()) == []


def test_conversation_runtime_registers_enabled_builtin_hooks(tmp_path: Path) -> None:
    """When config.builtin_hooks.enabled is non-empty, runtime registers them."""
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.config import BuiltinHooksConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry

    runner = HookRunner()

    @dataclass
    class _Cfg:
        max_turn_iterations: int = 5
        max_tokens: int = 4096
        temperature: float = 0.7
        native_tools: bool = True
        compact_after_tokens: int = 80000
        model: str = ""
        provider_base_url: str | None = None
        builtin_hooks: BuiltinHooksConfig = field(
            default_factory=lambda: BuiltinHooksConfig(enabled=("auto_format",))
        )

    session = Session.create(tmp_path)
    context = ProjectContext(cwd=tmp_path, instructions="", is_git_repo=False, git_status="")

    class _Provider:
        def supports_native_tools(self) -> bool:
            return True

        def supports_images(self) -> bool:
            return False

        def supports_reasoning(self) -> bool:
            return False

    ConversationRuntime(
        provider=_Provider(),
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=runner,
        prompt_builder=SystemPromptBuilder(),
        config=_Cfg(),
        session=session,
        context=context,
    )
    # auto_format subscribes to post_tool_use; runner should now have at least 1 subscriber
    assert runner._subscribers, "expected auto_format hook to be registered"


def test_conversation_runtime_skips_when_builtin_hooks_empty(tmp_path: Path) -> None:
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.config import BuiltinHooksConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry

    runner = HookRunner()

    @dataclass
    class _Cfg:
        max_turn_iterations: int = 5
        max_tokens: int = 4096
        temperature: float = 0.7
        native_tools: bool = True
        compact_after_tokens: int = 80000
        model: str = ""
        provider_base_url: str | None = None
        builtin_hooks: BuiltinHooksConfig = field(default_factory=BuiltinHooksConfig)

    session = Session.create(tmp_path)
    context = ProjectContext(cwd=tmp_path, instructions="", is_git_repo=False, git_status="")

    class _Provider:
        def supports_native_tools(self) -> bool:
            return True

        def supports_images(self) -> bool:
            return False

        def supports_reasoning(self) -> bool:
            return False

    before = list(runner._subscribers.items()) if hasattr(runner, "_subscribers") else []
    ConversationRuntime(
        provider=_Provider(),
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=runner,
        prompt_builder=SystemPromptBuilder(),
        config=_Cfg(),
        session=session,
        context=context,
    )
    after = list(runner._subscribers.items()) if hasattr(runner, "_subscribers") else []
    assert before == after  # nothing got added by runtime


# ---------------------------------------------------------------------------
# Task 4 — keyword action firing in run_turn
# ---------------------------------------------------------------------------


class _RecordingHooks:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def fire(self, event: str, context: dict) -> None:
        self.events.append((event, dict(context)))


def _make_runtime_for_keywords(tmp_path: Path, *, kw_enabled: bool):
    from dataclasses import dataclass, field
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.config import KeywordsConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry

    @dataclass
    class _Cfg:
        max_turn_iterations: int = 5
        max_tokens: int = 4096
        temperature: float = 0.7
        native_tools: bool = True
        compact_after_tokens: int = 80000
        model: str = ""
        provider_base_url: str | None = None
        keywords: KeywordsConfig = field(
            default_factory=lambda: KeywordsConfig(enabled=kw_enabled)
        )

    class _Provider:
        def supports_native_tools(self) -> bool: return True
        def supports_images(self) -> bool: return False
        def supports_reasoning(self) -> bool: return False

    hooks = _RecordingHooks()
    rt = ConversationRuntime(
        provider=_Provider(),
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=hooks,
        prompt_builder=SystemPromptBuilder(),
        config=_Cfg(),
        session=Session.create(tmp_path),
        context=ProjectContext(cwd=tmp_path, instructions="", is_git_repo=False, git_status=""),
    )
    return rt, hooks


def _trigger_keyword_path(rt, hooks, message: str) -> None:
    """Replicate just the keyword detection block (avoids running a full turn)."""
    rt._fire_hook("prompt_submit", {"text": message[:200]})
    _kw_cfg = getattr(rt._config, "keywords", None)
    if _kw_cfg is not None and getattr(_kw_cfg, "enabled", False):
        from llm_code.runtime.keyword_actions import detect_action
        action = detect_action(message)
        if action:
            rt._fire_hook("keyword_action", {"action": action, "message": message[:200]})


def test_keyword_action_fires_when_enabled(tmp_path: Path) -> None:
    rt, hooks = _make_runtime_for_keywords(tmp_path, kw_enabled=True)
    _trigger_keyword_path(rt, hooks, "please refactor this module")
    keyword_events = [e for e in hooks.events if e[0] == "keyword_action"]
    assert len(keyword_events) == 1
    assert keyword_events[0][1]["action"] == "trigger_refactor_persona"


def test_no_keyword_no_event(tmp_path: Path) -> None:
    rt, hooks = _make_runtime_for_keywords(tmp_path, kw_enabled=True)
    _trigger_keyword_path(rt, hooks, "hello world")
    assert not [e for e in hooks.events if e[0] == "keyword_action"]


def test_keyword_disabled_no_event(tmp_path: Path) -> None:
    rt, hooks = _make_runtime_for_keywords(tmp_path, kw_enabled=False)
    _trigger_keyword_path(rt, hooks, "please refactor this module")
    assert not [e for e in hooks.events if e[0] == "keyword_action"]

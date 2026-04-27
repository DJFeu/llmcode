"""Unit tests for ``CommandDispatcher`` — batch A (core commands).

Drives the dispatcher through a ``StubRecordingBackend`` and a
``SimpleNamespace`` state with a ``FakeRuntime``. Each test asserts
on the view calls (``print_info``, ``clear_screen``, ``request_exit``,
...) and any state mutation (``state.plan_mode``, ``state.cwd``,
``state.budget``).

Organized by command. Future batches add more test classes as the
dispatcher grows.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, List, Optional
from unittest.mock import MagicMock

import pytest

from llm_code.view.dispatcher import CommandDispatcher
from llm_code.view.stream_renderer import ViewStreamRenderer

from tests.test_view._stub_backend import StubRecordingBackend


# === Shared fakes ===


class FakeRuntime:
    """Minimal runtime stub. Supports attributes CommandDispatcher
    touches on batch A: ``plan_mode``, ``_permissions``, ``_cancel``,
    ``run_turn``."""

    def __init__(
        self,
        events: Optional[List[Any]] = None,
        *,
        permissions: Any = None,
        cancel_raises: bool = False,
    ) -> None:
        self._events = events or []
        self.plan_mode = False
        self._permissions = permissions
        self._cancel_called = False
        self._cancel_raises = cancel_raises
        self._skill_router = None
        self._model_profile = None
        self.session = SimpleNamespace(messages=[])
        self._force_xml_mode = False
        self._config: Any = None

    def _cancel(self) -> None:
        if self._cancel_raises:
            raise RuntimeError("cancel boom")
        self._cancel_called = True

    async def run_turn(
        self,
        user_input: str,
        images: Any = None,
        active_skill_content: Any = None,
    ) -> AsyncIterator[Any]:
        for ev in self._events:
            yield ev

    def send_permission_response(self, *args, **kwargs) -> None:
        pass


class FakePermissionPolicy:
    """Mimics the PermissionPolicy public surface. ``_mode`` is
    mutated by /yolo and /mode (historically directly, now via
    ``switch_to`` so the ModeTransition event is recorded)."""

    def __init__(self, mode: Any) -> None:
        self._mode = mode
        self._last_transition = None

    @property
    def mode(self) -> Any:
        return self._mode

    def switch_to(self, target: Any) -> Any:
        if target is self._mode:
            return None
        from llm_code.runtime.permissions import ModeTransition
        event = ModeTransition(from_mode=self._mode, to_mode=target)
        self._mode = target
        self._last_transition = event
        return event

    def last_transition(self) -> Any:
        return self._last_transition

    def consume_last_transition(self) -> Any:
        event = self._last_transition
        self._last_transition = None
        return event


def _make_state(
    tmp_path: Path,
    *,
    runtime: Any = None,
    cost_tracker: Any = None,
    config: Any = None,
    skills: Any = None,
    checkpoint_mgr: Any = None,
    budget: Optional[int] = None,
) -> SimpleNamespace:
    """Build an AppState-shaped SimpleNamespace for dispatcher tests."""
    return SimpleNamespace(
        cwd=tmp_path,
        budget=budget,
        config=config,
        runtime=runtime,
        cost_tracker=cost_tracker,
        skills=skills,
        checkpoint_mgr=checkpoint_mgr,
        plan_mode=False,
        tool_reg=MagicMock(all_tools=lambda: []),
        input_tokens=0,
        output_tokens=0,
        last_stop_reason="unknown",
        context_warned=False,
    )


@pytest.fixture
def backend() -> StubRecordingBackend:
    return StubRecordingBackend()


@pytest.fixture
def dispatcher_factory(backend, tmp_path: Path):
    """Build a CommandDispatcher wired to a StubRecordingBackend
    and a state you pass in."""
    def _make(
        *,
        state: Optional[SimpleNamespace] = None,
        events: Optional[List[Any]] = None,
    ) -> CommandDispatcher:
        if state is None:
            runtime = FakeRuntime(events=events)
            state = _make_state(tmp_path, runtime=runtime)
        renderer = ViewStreamRenderer(view=backend, state=state)
        return CommandDispatcher(view=backend, state=state, renderer=renderer)

    return _make


# === dispatch() basics ===


def test_dispatch_returns_true_on_known(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    assert d.dispatch("clear", "") is True
    assert backend.clears == 1


def test_dispatch_returns_false_on_unknown(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    assert d.dispatch("__definitely_not_a_command__", "") is False
    # No print side-effect on a bare dispatch miss — the caller
    # decides what to do with it.
    assert backend.error_lines == []


def test_dispatch_catches_handler_exception(
    dispatcher_factory, backend, tmp_path,
) -> None:
    """A handler that raises must not escape dispatch — it's caught
    and surfaced via print_error so the REPL loop stays alive."""
    d = dispatcher_factory()

    def boom(args):
        raise RuntimeError("nope")

    d._cmd_boom = boom  # type: ignore[attr-defined]
    assert d.dispatch("boom", "") is True
    assert any("/boom failed" in e for e in backend.error_lines)
    assert any("nope" in e for e in backend.error_lines)


# === run_turn top-level routing ===


@pytest.mark.asyncio
async def test_run_turn_empty_input_is_noop(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    await d.run_turn("")
    assert backend.info_lines == []
    assert backend.error_lines == []


@pytest.mark.asyncio
async def test_run_turn_whitespace_input_is_noop(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    await d.run_turn("   \n  ")
    assert backend.info_lines == []


@pytest.mark.asyncio
async def test_run_turn_slash_command_dispatches(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    await d.run_turn("/clear")
    assert backend.clears == 1


@pytest.mark.asyncio
async def test_run_turn_plain_text_calls_renderer(
    dispatcher_factory, backend, tmp_path,
) -> None:
    runtime = FakeRuntime(events=[])
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    await d.run_turn("hello")

    # Renderer should have run — on_turn_start + on_turn_end fired
    assert backend.turn_starts == 1
    assert backend.turn_ends == 1


@pytest.mark.asyncio
async def test_run_turn_unknown_slash_shows_close_match_hint(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    await d.run_turn("/clea")  # typo for /clear
    # Should get a warning with either a close-match or a /help hint
    assert backend.warning_lines
    msg = backend.warning_lines[0]
    assert "Unknown command" in msg
    assert "/clea" in msg


@pytest.mark.asyncio
async def test_run_turn_renderer_exception_is_caught(
    dispatcher_factory, backend, tmp_path,
) -> None:
    """If ViewStreamRenderer raises (not a StreamEvent exception but
    an outer-level crash), the dispatcher catches and surfaces it."""
    d = dispatcher_factory()

    async def boom(text, images=None):
        raise RuntimeError("renderer crash")

    d._renderer = SimpleNamespace(run_turn=boom)
    await d.run_turn("plain text")
    assert any("turn failed" in e for e in backend.error_lines)
    assert any("renderer crash" in e for e in backend.error_lines)


@pytest.mark.asyncio
async def test_custom_command_routes_through_renderer(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """When a slash command isn't a built-in, the dispatcher checks
    ``.llmcode/commands/`` and runs the rendered prompt through the
    renderer."""
    from llm_code.runtime import custom_commands

    fake_cmd = SimpleNamespace(render=lambda args: "expanded prompt")

    def fake_discover(cwd):
        return {"mycmd": fake_cmd}

    monkeypatch.setattr(
        custom_commands, "discover_custom_commands", fake_discover,
    )
    d = dispatcher_factory()
    await d.run_turn("/mycmd arg1 arg2")

    assert any("Running custom command: /mycmd" in i for i in backend.info_lines)
    assert backend.turn_starts == 1  # renderer fired


@pytest.mark.asyncio
async def test_skill_command_routes_through_renderer(
    dispatcher_factory, backend, tmp_path,
) -> None:
    """When a slash command matches a loaded command skill's trigger,
    the dispatcher injects the skill content into the renderer."""
    skill = SimpleNamespace(
        name="test-skill",
        trigger="tskill",
        content="skill prompt body",
    )
    skills = SimpleNamespace(
        command_skills=[skill],
        auto_skills=[],
    )
    runtime = FakeRuntime(events=[])
    state = _make_state(tmp_path, runtime=runtime, skills=skills)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    await d.run_turn("/tskill argument")

    assert any("Activated skill: test-skill" in i for i in backend.info_lines)
    assert backend.turn_starts == 1
    assert runtime._events == []  # no events but run_turn was called


# === /help ===


def test_help_lists_builtin_commands(dispatcher_factory, backend) -> None:
    d = dispatcher_factory()
    d.dispatch("help", "")
    assert backend.info_lines
    help_text = backend.info_lines[0]
    # Check a sampling of known commands
    assert "/clear" in help_text
    assert "/model" in help_text
    assert "/exit" in help_text


def test_help_lists_skill_commands_when_present(
    dispatcher_factory, backend, tmp_path,
) -> None:
    skill = SimpleNamespace(
        name="python-patterns",
        trigger="py",
        description="Python patterns skill",
        content="...",
    )
    skills = SimpleNamespace(
        command_skills=[skill],
        auto_skills=[],
    )
    state = _make_state(tmp_path, skills=skills)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("help", "")

    help_text = backend.info_lines[0]
    assert "Command skills:" in help_text
    assert "/py" in help_text
    assert "Python patterns skill" in help_text


# === /clear ===


def test_clear_calls_view_clear_screen(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("clear", "")
    assert backend.clears == 1


# === /exit and /quit ===


def test_exit_calls_request_exit(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("exit", "")
    # StubRecordingBackend.request_exit sets _running=False
    assert backend._running is False


def test_quit_is_alias_for_exit(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("quit", "")
    assert backend._running is False


# === /cost ===


def test_cost_prints_tracker_format(
    dispatcher_factory, backend, tmp_path,
) -> None:
    tracker = MagicMock(format_cost=MagicMock(return_value="$0.1234"))
    state = _make_state(tmp_path, cost_tracker=tracker)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("cost", "")
    assert "$0.1234" in backend.info_lines[0]


def test_cost_without_tracker_prints_message(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, cost_tracker=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("cost", "")
    assert "No cost data" in backend.info_lines[0]


# === /cancel ===


def test_cancel_invokes_runtime_cancel(
    dispatcher_factory, backend, tmp_path,
) -> None:
    runtime = FakeRuntime()
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("cancel", "")
    assert runtime._cancel_called is True
    assert any("(cancelled)" in i for i in backend.info_lines)


def test_cancel_without_runtime_still_prints(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("cancel", "")
    assert any("(cancelled)" in i for i in backend.info_lines)


# === /cd ===


def test_cd_without_args_shows_current(
    dispatcher_factory, backend, tmp_path,
) -> None:
    d = dispatcher_factory()
    d.dispatch("cd", "")
    assert any(str(tmp_path) in i for i in backend.info_lines)


def test_cd_to_existing_dir_mutates_state_and_chdir(
    dispatcher_factory, backend, tmp_path,
) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()
    original_cwd = os.getcwd()
    try:
        d = dispatcher_factory()
        d.dispatch("cd", str(subdir))
        assert d._state.cwd == subdir.resolve()
        assert Path(os.getcwd()).resolve() == subdir.resolve()
    finally:
        os.chdir(original_cwd)


def test_cd_to_missing_dir_prints_error(
    dispatcher_factory, backend, tmp_path,
) -> None:
    d = dispatcher_factory()
    d.dispatch("cd", "nonexistent_dir")
    assert any("Directory not found" in e for e in backend.error_lines)


# === /budget ===


def test_budget_without_args_and_no_budget_set(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("budget", "")
    assert any("No budget set" in i for i in backend.info_lines)


def test_budget_set_updates_state(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("budget", "8192")
    assert d._state.budget == 8192
    assert any("8,192" in i for i in backend.info_lines)


def test_budget_non_integer_prints_usage(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("budget", "abc")
    assert any("Usage" in e for e in backend.error_lines)


def test_budget_without_args_shows_current(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, budget=4096)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("budget", "")
    assert any("4,096" in i for i in backend.info_lines)


# === /plan ===


def test_plan_toggles_plan_mode(
    dispatcher_factory, backend, tmp_path,
) -> None:
    runtime = FakeRuntime()
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    assert state.plan_mode is False
    d.dispatch("plan", "")
    assert state.plan_mode is True
    assert runtime.plan_mode is True
    assert any("Plan mode ON" in i for i in backend.info_lines)

    d.dispatch("plan", "")
    assert state.plan_mode is False
    assert runtime.plan_mode is False
    assert any("Plan mode OFF" in i for i in backend.info_lines)


# === /yolo ===


def test_yolo_toggles_runtime_permission_mode(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.permissions import PermissionMode

    policy = FakePermissionPolicy(mode=PermissionMode.PROMPT)
    runtime = FakeRuntime(permissions=policy)
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    d.dispatch("yolo", "")
    assert policy._mode == PermissionMode.AUTO_ACCEPT
    assert any("YOLO mode ON" in w for w in backend.warning_lines)

    d.dispatch("yolo", "")
    assert policy._mode == PermissionMode.PROMPT
    assert any("YOLO mode OFF" in i for i in backend.info_lines)


def test_yolo_without_runtime_prints_error(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("yolo", "")
    assert any("Runtime not initialized" in e for e in backend.error_lines)


# === /mode ===


def test_mode_without_args_shows_current(
    dispatcher_factory, backend, tmp_path,
) -> None:
    d = dispatcher_factory()
    d.dispatch("mode", "")
    msg = backend.info_lines[0]
    assert "Current mode" in msg
    assert "normal" in msg


def test_mode_switches_to_plan(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.permissions import PermissionMode

    policy = FakePermissionPolicy(mode=PermissionMode.PROMPT)
    runtime = FakeRuntime(permissions=policy)
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    d.dispatch("mode", "plan")
    assert state.plan_mode is True
    assert policy._mode == PermissionMode.PLAN
    assert runtime.plan_mode is True


def test_mode_unknown_prints_error(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("mode", "banana")
    assert any("Unknown mode" in e for e in backend.error_lines)


# === /thinking ===


def test_thinking_on_updates_config(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig

    config = RuntimeConfig()
    runtime = FakeRuntime()
    runtime._config = config
    state = _make_state(tmp_path, runtime=runtime, config=config)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    d.dispatch("thinking", "on")
    assert state.config.thinking.mode == "enabled"
    assert runtime._config.thinking.mode == "enabled"


def test_thinking_without_args_shows_current(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig

    state = _make_state(tmp_path, config=RuntimeConfig())
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("thinking", "")
    assert any("Thinking:" in i for i in backend.info_lines)


def test_thinking_invalid_arg_shows_usage(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig

    state = _make_state(tmp_path, config=RuntimeConfig())
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("thinking", "maybe")
    # "maybe" isn't in mode_map, so it falls through to the info path
    assert any("Usage" in i for i in backend.info_lines)


# === /profile ===


def test_profile_without_runtime(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("profile", "")
    assert any("profiler not initialized" in i for i in backend.info_lines)


def test_profile_with_profiler_prints_breakdown(
    dispatcher_factory, backend, tmp_path,
) -> None:
    runtime = FakeRuntime()
    runtime._query_profiler = SimpleNamespace(
        format_breakdown=lambda pricing: "PROFILER OUTPUT"
    )
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("profile", "")
    assert any("PROFILER OUTPUT" in i for i in backend.info_lines)


# === /diff ===


def test_diff_without_checkpoint_mgr(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, checkpoint_mgr=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("diff", "")
    assert any("No checkpoints available" in i for i in backend.info_lines)


# === run_turn smoke: every core command is dispatchable ===


@pytest.mark.parametrize(
    "name",
    [
        "help",
        "clear",
        "exit",
        "quit",
        "cost",
        "cancel",
        "cd",
        "budget",
        "plan",
        "mode",
        "thinking",
        "gain",
        "profile",
        "diff",
    ],
)
def test_every_batch_a_command_is_registered(
    dispatcher_factory, name: str,
) -> None:
    """Sanity: all batch A commands must be dispatchable. Any rename
    that breaks this coverage will surface immediately."""
    d = dispatcher_factory()
    # dispatch returns True on known, False on unknown
    assert d.dispatch(name, "") is True or d.dispatch(name, "") is True


# === Batch B: runtime / config / state mutation ===


def test_compact_without_runtime_errors(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("compact", "")
    assert any("Compaction unavailable" in e for e in backend.error_lines)


def test_export_empty_session_prints_info(
    dispatcher_factory, backend, tmp_path,
) -> None:
    # FakeRuntime.session has an empty messages list
    runtime = FakeRuntime()
    state = _make_state(tmp_path, runtime=runtime)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("export", "")
    assert any(
        "conversation is empty" in i for i in backend.info_lines
    )


def test_undo_without_checkpoint_mgr(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, checkpoint_mgr=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("undo", "")
    assert any("not in a git" in i.lower() for i in backend.info_lines)


def test_model_without_args_shows_current(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig(model="claude-4.6")
    state = _make_state(tmp_path, config=cfg)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("model", "")
    assert any("claude-4.6" in i for i in backend.info_lines)


def test_model_switch_mutates_config(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig(model="old-model")
    runtime = FakeRuntime()
    runtime._config = cfg
    state = _make_state(tmp_path, runtime=runtime, config=cfg)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("model", "new-model")
    assert state.config.model == "new-model"
    assert runtime._config.model == "new-model"


def test_model_route_shows_routing_table(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig(model="primary")
    state = _make_state(tmp_path, config=cfg)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("model", "route")
    info = "\n".join(backend.info_lines)
    assert "Model routing" in info or "No model routing" in info


def test_cache_list_prints_header(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("cache", "")
    assert any("Persistent caches" in i for i in backend.info_lines)


def test_cache_unknown_sub_shows_usage(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("cache", "banana")
    assert any("Usage" in i for i in backend.info_lines)


def test_theme_lists_when_no_arg(dispatcher_factory, backend) -> None:
    """v16 M4: /theme is no longer a stub — empty arg lists themes."""
    d = dispatcher_factory()
    d.dispatch("theme", "")
    info = "\n".join(backend.info_lines)
    assert "Themes:" in info
    # Spot-check three of the eight named themes.
    for name in ("default", "dracula", "nord"):
        assert name in info


def test_config_without_config_prints_message(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, config=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("config", "")
    assert any("No config loaded" in i for i in backend.info_lines)


def test_config_prints_summary(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    state = _make_state(tmp_path, config=RuntimeConfig(model="m1"))
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("config", "")
    info = "\n".join(backend.info_lines)
    assert "model: m1" in info
    assert "thinking" in info


def test_set_without_args_shows_usage(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    state = _make_state(tmp_path, config=RuntimeConfig())
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("set", "")
    assert any("Usage: /set" in i for i in backend.info_lines)


def test_settings_without_config(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, config=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("settings", "")
    assert any("No config loaded" in i for i in backend.info_lines)


def test_index_without_index_shows_message(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.project_index = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("index", "")
    assert any("No index available" in i for i in backend.info_lines)


def test_harness_without_runtime(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("harness", "")
    assert any("not available" in i.lower() for i in backend.info_lines)


def test_session_prints_hint(dispatcher_factory, backend) -> None:
    d = dispatcher_factory()
    d.dispatch("session", "")
    assert any("/checkpoint" in i for i in backend.info_lines)


def test_checkpoint_list_empty(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    from llm_code.runtime import checkpoint_recovery

    class FakeRecovery:
        def __init__(self, *a, **kw): pass
        def list_checkpoints(self): return []
        def save_checkpoint(self, session): return "path"
        def load_checkpoint(self, sid, cost_tracker=None): return None
        def detect_last_checkpoint(self, cost_tracker=None): return None

    monkeypatch.setattr(
        checkpoint_recovery, "CheckpointRecovery", FakeRecovery,
    )
    d = dispatcher_factory()
    d.dispatch("checkpoint", "list")
    assert any("No checkpoints found" in i for i in backend.info_lines)


# === Batch C: feature modules ===


def test_search_without_query_shows_usage(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("search", "")
    assert any("Usage: /search" in i for i in backend.info_lines)


def test_memory_not_initialized(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.memory = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("memory", "")
    assert any("Memory not initialized" in i for i in backend.info_lines)


def test_memory_set_and_get(
    dispatcher_factory, backend, tmp_path,
) -> None:
    class FakeMemory:
        def __init__(self):
            self._data = {}
        def store(self, k, v):
            self._data[k] = v
        def recall(self, k):
            return self._data.get(k)
        def delete(self, k):
            self._data.pop(k, None)
        def get_all(self):
            class _Entry:
                def __init__(self, v): self.value = v
            return {k: _Entry(v) for k, v in self._data.items()}
        def load_consolidated_summaries(self, limit):
            return []

    state = _make_state(tmp_path)
    state.memory = FakeMemory()
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("memory", "set key hello world")
    assert any("Stored: key" in i for i in backend.info_lines)
    backend.info_lines.clear()
    d.dispatch("memory", "get key")
    assert any("hello world" in i for i in backend.info_lines)


def test_mcp_list_default(
    dispatcher_factory, backend, tmp_path,
) -> None:
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig(mcp_servers={"foo": {"command": "npx", "args": ["-y", "foo-pkg"]}})
    state = _make_state(tmp_path, config=cfg)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("mcp", "")
    info = "\n".join(backend.info_lines)
    assert "foo" in info
    assert "Usage: /mcp" in info


def test_ide_not_configured(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.ide_bridge = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("ide", "")
    assert any("disabled" in i.lower() for i in backend.info_lines)


def test_hida_without_runtime(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("hida", "")
    assert any("not initialized" in i for i in backend.info_lines)


def test_lsp_not_started(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.lsp_manager = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("lsp", "")
    assert any("not started" in i for i in backend.info_lines)


def test_skill_list_default(
    dispatcher_factory, backend, tmp_path,
) -> None:
    skills = SimpleNamespace(auto_skills=[], command_skills=[])
    state = _make_state(tmp_path, skills=skills)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("skill", "")
    assert any("Installed skills" in i for i in backend.info_lines)


def test_skill_install_invalid_repo(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("skill", "install not-a-repo-format")
    assert any("Usage: /skill install" in e for e in backend.error_lines)


def test_plugin_install_invalid_repo(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("plugin", "install not-a-repo")
    assert any("Usage: /plugin install" in e for e in backend.error_lines)


def test_voice_not_configured(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.voice_active = False
    state.voice_recorder = None
    state.voice_stt = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("voice", "")
    assert any("not configured" in i for i in backend.info_lines)


def test_cron_not_available(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.cron_storage = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("cron", "")
    assert any("not available" in i.lower() for i in backend.info_lines)


def test_task_default_hint(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.task_manager = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("task", "")
    assert any("task tools" in i for i in backend.info_lines)


def test_swarm_not_enabled(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path)
    state.swarm_manager = None
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("swarm", "")
    assert any("not enabled" in i for i in backend.info_lines)


def test_vcr_default_status(
    dispatcher_factory, backend, tmp_path,
) -> None:
    d = dispatcher_factory()
    d.dispatch("vcr", "")
    assert any("VCR:" in i for i in backend.info_lines)


def test_personas_lists_builtin(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("personas", "")
    assert any("built-in personas" in i for i in backend.info_lines)


def test_orchestrate_without_task_shows_usage(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("orchestrate", "")
    assert any("Usage: /orchestrate" in i for i in backend.info_lines)


def test_map_handles_error(
    dispatcher_factory, backend, tmp_path,
) -> None:
    """The /map happy path requires a real repo; assert it doesn't
    crash and at least prints an info or error line."""
    d = dispatcher_factory()
    d.dispatch("map", "")
    # Either an info line or an error line — we just want no crash
    assert backend.info_lines or backend.error_lines


# === Batch D: copy / image / vim ===


def test_copy_without_runtime_prints_message(
    dispatcher_factory, backend, tmp_path,
) -> None:
    state = _make_state(tmp_path, runtime=None)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("copy", "")
    assert any("No response to copy" in i for i in backend.info_lines)


def test_image_without_args_shows_usage(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("image", "")
    assert any("Usage: /image" in i for i in backend.info_lines)


def test_image_missing_file(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("image", "/nonexistent/absolutely/not/here.png")
    assert any("Image not found" in e or "Image load failed" in e for e in backend.error_lines)


def test_vim_prints_info_hint(
    dispatcher_factory, backend,
) -> None:
    d = dispatcher_factory()
    d.dispatch("vim", "")
    assert any("Vim mode" in i for i in backend.info_lines)


# === Full registry smoke: every v1 command name is present ===


@pytest.mark.parametrize(
    "name",
    [
        "compact", "export", "update", "theme", "model", "cache",
        "profile", "gain", "diff", "init", "index", "thinking",
        "vim", "image", "lsp", "cancel", "plan", "yolo", "mode",
        "harness", "knowledge", "dump", "analyze", "diff_check",
        "search", "set", "settings", "config", "session", "voice",
        "cron", "task", "personas", "orchestrate", "swarm", "vcr",
        "checkpoint", "memory", "map", "mcp", "ide", "hida",
        "skill", "plugin", "copy", "undo", "help", "clear",
        "exit", "quit", "cost", "cd", "budget",
    ],
)
def test_every_v1_command_is_registered(
    dispatcher_factory, name: str,
) -> None:
    """Full M10.6 coverage: every command from v1's
    ``tui/command_dispatcher.py`` (52 total + /quit alias) must be
    registered on ``CommandDispatcher``."""
    d = dispatcher_factory()
    handler = getattr(d, f"_cmd_{name}", None)
    assert handler is not None, f"missing handler: _cmd_{name}"


def test_mcp_install_writes_mcpServers_camelcase(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """v2.5.3 — /mcp install must write the canonical ``mcpServers``
    (camelCase) key that ``config.py`` reads. Pre-v2.5.3 wrote
    ``mcp_servers`` (snake_case) which the loader silently ignored,
    so installs disappeared on next startup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    state = _make_state(tmp_path)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)

    d.dispatch("mcp", "install @modelcontextprotocol/server-filesystem")

    import json
    config_path = tmp_path / ".llmcode" / "config.json"
    assert config_path.exists(), "config.json should be created"
    data = json.loads(config_path.read_text())
    assert "mcpServers" in data, "must use canonical mcpServers key"
    assert "mcp_servers" not in data, "must NOT write legacy snake_case key"
    assert "server-filesystem" in data["mcpServers"]


def test_mcp_install_migrates_legacy_snake_case_key(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """v2.5.3 — when an existing config still has the pre-v2.5.3
    ``mcp_servers`` key from a prior buggy install, the new install
    must merge those entries forward into ``mcpServers`` so they
    survive."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".llmcode" / "config.json"
    config_path.parent.mkdir(parents=True)
    import json
    config_path.write_text(json.dumps({
        "mcp_servers": {
            "legacy_server": {"command": "npx", "args": ["-y", "old-pkg"]},
        },
    }))

    state = _make_state(tmp_path)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("mcp", "install new-server-pkg")

    data = json.loads(config_path.read_text())
    assert "mcpServers" in data
    assert "mcp_servers" not in data, "legacy key must be migrated, not duplicated"
    assert "legacy_server" in data["mcpServers"], "legacy entry must survive"
    assert "new-server-pkg" in data["mcpServers"]


def test_config_loader_accepts_legacy_snake_case_mcp_key(tmp_path) -> None:
    """v2.5.3 — config loader merges ``mcp_servers`` entries forward
    so users on old configs (written by the pre-v2.5.3 /mcp install
    bug) don't lose their MCP servers on the next startup."""
    from llm_code.runtime.config import _dict_to_runtime_config
    raw = {
        "model": "glm-5.1",
        "mcp_servers": {
            "snake_server": {"command": "npx", "args": ["-y", "x"]},
        },
        "mcpServers": {
            "camel_server": {"command": "npx", "args": ["-y", "y"]},
        },
    }
    cfg = _dict_to_runtime_config(raw)
    assert "snake_server" in cfg.mcp_servers
    assert "camel_server" in cfg.mcp_servers


def test_mcp_install_respects_split_schema(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """v2.5.4 — when the user's config already uses the documented
    ``always_on`` / ``on_demand`` split schema, /mcp install must
    insert the new server INTO ``always_on`` (not at the top level
    next to those keys, where the loader's split branch ignores
    everything except those two keys)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".llmcode" / "config.json"
    config_path.parent.mkdir(parents=True)
    import json
    config_path.write_text(json.dumps({
        "mcpServers": {
            "always_on": {
                "existing_server": {"command": "npx", "args": ["-y", "exists"]},
            },
            "on_demand": {},
        },
    }))

    state = _make_state(tmp_path)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("mcp", "install new-pkg")

    data = json.loads(config_path.read_text())
    assert "always_on" in data["mcpServers"]
    assert "existing_server" in data["mcpServers"]["always_on"], (
        "existing always_on entries must survive the install"
    )
    assert "new-pkg" in data["mcpServers"]["always_on"], (
        "new server must land inside always_on, not next to it where "
        "the loader's split branch would ignore it"
    )
    assert "new-pkg" not in data["mcpServers"], (
        "new server must NOT live at the top level when split schema "
        "is in use"
    )

    # Verify the loader actually sees the new entry.
    from llm_code.runtime.config import _dict_to_runtime_config
    cfg = _dict_to_runtime_config({"model": "x", **data})
    assert "new-pkg" in cfg.mcp_servers, (
        "loader must surface the newly-installed server"
    )


def test_mcp_remove_finds_split_schema_entry(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """v2.5.4 — /mcp remove must search both top-level and the split
    sub-dicts so it works regardless of which install version (or
    schema) put the entry there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".llmcode" / "config.json"
    config_path.parent.mkdir(parents=True)
    import json
    config_path.write_text(json.dumps({
        "mcpServers": {
            "always_on": {
                "to_remove": {"command": "npx", "args": ["-y", "x"]},
            },
            "on_demand": {
                "lazy_one": {"command": "npx", "args": ["-y", "y"]},
            },
        },
    }))

    state = _make_state(tmp_path)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("mcp", "remove to_remove")

    data = json.loads(config_path.read_text())
    assert "to_remove" not in data["mcpServers"]["always_on"]
    # on_demand sibling must remain untouched.
    assert "lazy_one" in data["mcpServers"]["on_demand"]


def test_loader_promotes_stranded_top_level_into_always_on(tmp_path) -> None:
    """v2.5.5 — when a config has split schema (``always_on`` and/or
    ``on_demand``) PLUS sibling top-level server entries (left over
    from pre-v2.5.4 ``/mcp install`` writes), the loader must promote
    those siblings into ``always_on`` instead of dropping them."""
    from llm_code.runtime.config import _dict_to_runtime_config
    raw = {
        "model": "x",
        "mcpServers": {
            "always_on": {
                "explicit_server": {"command": "npx", "args": ["-y", "a"]},
            },
            "on_demand": {
                "lazy_server": {"command": "npx", "args": ["-y", "lazy"]},
            },
            # Stranded by a prior buggy install — must be rescued.
            "stranded_server": {"command": "npx", "args": ["-y", "rescued"]},
        },
    }
    cfg = _dict_to_runtime_config(raw)
    assert "explicit_server" in cfg.mcp_servers
    assert "stranded_server" in cfg.mcp_servers, (
        "stranded top-level entry must be promoted to always_on, not "
        "dropped by the loader's split branch"
    )
    # on_demand stays in its own slot (not flattened into always_on
    # because it's a lazy / opt-in surface).
    assert "lazy_server" not in cfg.mcp_servers
    assert "lazy_server" in cfg.mcp.on_demand


def test_explicit_always_on_wins_over_stranded(tmp_path) -> None:
    """When the same key exists both at the top level (stranded) and
    inside ``always_on`` (explicit), the explicit declaration wins —
    a user re-declaring a server should not get clobbered by a stale
    sibling left over from a prior install."""
    from llm_code.runtime.config import _dict_to_runtime_config
    raw = {
        "model": "x",
        "mcpServers": {
            "always_on": {
                "shared_name": {"command": "npx", "args": ["-y", "winner"]},
            },
            "shared_name": {"command": "npx", "args": ["-y", "loser"]},
        },
    }
    cfg = _dict_to_runtime_config(raw)
    assert cfg.mcp_servers["shared_name"]["args"] == ["-y", "winner"]


def test_install_rescues_stranded_top_level_entries(
    dispatcher_factory, backend, tmp_path, monkeypatch,
) -> None:
    """v2.5.5 — running ``/mcp install`` on a config with stranded
    top-level entries (from pre-v2.5.4 installs) MUST migrate them
    into ``always_on`` on disk so the config self-heals — not just
    on the next reload, but in the persisted file too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".llmcode" / "config.json"
    config_path.parent.mkdir(parents=True)
    import json
    config_path.write_text(json.dumps({
        "mcpServers": {
            "always_on": {
                "explicit": {"command": "npx", "args": ["-y", "x"]},
            },
            "on_demand": {},
            "stranded": {"command": "npx", "args": ["-y", "y"]},
        },
    }))

    state = _make_state(tmp_path)
    renderer = ViewStreamRenderer(view=backend, state=state)
    d = CommandDispatcher(view=backend, state=state, renderer=renderer)
    d.dispatch("mcp", "install fresh-pkg")

    data = json.loads(config_path.read_text())
    assert "stranded" not in data["mcpServers"], (
        "stranded entry must be moved out of the top level"
    )
    assert "stranded" in data["mcpServers"]["always_on"], (
        "stranded entry must be migrated into always_on"
    )
    assert "fresh-pkg" in data["mcpServers"]["always_on"]
    assert "explicit" in data["mcpServers"]["always_on"]

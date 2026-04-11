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
    """Mimics the v1.x PermissionPolicy public surface. ``_mode`` is
    mutated by /yolo and /mode."""

    def __init__(self, mode: Any) -> None:
        self._mode = mode


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

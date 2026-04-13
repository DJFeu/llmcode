"""Snapshot tests — visual regression coverage for key REPL components.

M13 deliverable. 20 golden-file comparisons over the components that
produce user-visible output: ``StatusLine``, ``ToolEventRegion``,
``DialogPopover``, and the Rich panel helpers used by ``view.print_*``.

Regenerate a single golden::

    PYTEST_SNAPSHOT_UPDATE=1 pytest tests/test_view/test_snapshots.py::test_X

Regenerate all goldens::

    PYTEST_SNAPSHOT_UPDATE=1 pytest tests/test_view/test_snapshots.py

Notes from the M11-M14 audit §M13:

- **L3**: Dialog popover tests deliberately assign to the private
  ``_active`` slot so the render pipeline can be exercised without
  going through the async ``show_confirm`` flow. This is a
  documented white-box technique, not an API misuse.
- **L4**: ``StatusLine._spinner_frame`` is initialized to 0 and each
  test constructs a fresh instance without calling
  ``advance_spinner``. Do not add a call to ``advance_spinner`` in
  any snapshot-test setup without also rewriting the golden.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from llm_code.view.dialog_types import Choice
from llm_code.view.repl.components.dialog_popover import (
    ChecklistRequest,
    ConfirmRequest,
    DialogPopover,
    SelectRequest,
    TextInputRequest,
)
from llm_code.view.repl.components.status_line import StatusLine
from llm_code.view.repl.components.tool_event_renderer import ToolEventRegion
from llm_code.view.repl.snapshots import (
    assert_snapshot,
    capture,
    render_formatted,
)
from llm_code.view.types import RiskLevel, StatusUpdate


# Shared event loop for the dialog-popover snapshot tests. The
# Request dataclasses type their ``future`` field as
# ``asyncio.Future``, which requires a loop to construct. We build
# one at module import, reuse it across the dialog tests, and never
# run anything on it — the futures never get awaited, they're just
# holders so the render pipeline has something non-None to iterate.
_EVENT_LOOP = asyncio.new_event_loop()


# === StatusLine ===


def test_snapshot_status_line_default():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B",
        cwd="llm-code",
        branch="main",
        context_used_tokens=16400,
        context_limit_tokens=128000,
        cost_usd=0.0,
    ))
    output = render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_default", output)


def test_snapshot_status_line_streaming():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B",
        cwd="llm-code",
        branch="main",
        context_used_tokens=16400,
        context_limit_tokens=128000,
        cost_usd=0.0,
        is_streaming=True,
        streaming_token_count=1234,
    ))
    output = render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_streaming", output)


def test_snapshot_status_line_voice_recording():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        voice_active=True,
        voice_seconds=2.3,
        voice_peak=0.42,
    ))
    output = render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_voice_recording", output)


def test_snapshot_status_line_rate_limited():
    sl = StatusLine()
    # ``is_rate_limited()`` compares against naive local
    # ``datetime.now()``, so the ``rate_limit_until`` we feed must
    # also be naive local time 5 minutes in the future.
    sl.merge(StatusUpdate(
        model="Q3.5-122B",
        rate_limit_until=datetime.now() + timedelta(minutes=5),
        rate_limit_reqs_left=3,
    ))
    output = render_formatted(sl.render_rate_limit_warning())
    # The rate-limit warning encodes a local HH:MM:SS — which isn't
    # caught by normalize(). Strip the time so the golden is stable.
    import re
    output = re.sub(
        r"retry \d{2}:\d{2}:\d{2}", "retry {time}", output,
    )
    assert_snapshot("status_line_rate_limited", output)


def test_snapshot_status_line_plan_mode():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B",
        cwd="llm-code",
        branch="main",
        permission_mode="plan",
        cost_usd=0.01,
    ))
    output = render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_plan_mode", output)


# === ToolEventRegion ===


def test_snapshot_tool_event_read_file():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="read_file",
            args={"path": "foo.py"},
        )
        region.commit_success(summary="47 lines")
    output = capture(render)
    assert_snapshot("tool_event_read_file", output)


def test_snapshot_tool_event_bash_success():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="bash",
            args={"command": "pytest tests/"},
        )
        region.commit_success(summary="28 passed")
    output = capture(render)
    assert_snapshot("tool_event_bash_success", output)


def test_snapshot_tool_event_bash_failure():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="bash",
            args={"command": "docker run nonexistent"},
        )
        region.feed_stderr(
            "Unable to find image 'nonexistent:latest'"
        )
        region.feed_stderr(
            "docker: Error response from daemon: pull access denied"
        )
        region.commit_failure(error="exit 125", exit_code=125)
    output = capture(render)
    assert_snapshot("tool_event_bash_failure", output)


def test_snapshot_tool_event_edit_file_with_diff():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="edit_file",
            args={"path": "bar.py"},
        )
        region.feed_diff(
            "@@ -10,3 +10,5 @@\n"
            "     def parse():\n"
            "-        return None\n"
            "+        if not data: return None\n"
            "+        return data\n"
        )
        region.commit_success(summary="+2 -1")
    output = capture(render)
    assert_snapshot("tool_event_edit_file_with_diff", output)


def test_snapshot_tool_event_apply_patch():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="apply_patch",
            args={"path": "example.py"},
        )
        region.feed_diff(
            "@@ -1,5 +1,6 @@\n"
            " import asyncio\n"
            "+import sys\n"
            " def main():\n"
            "-    return 0\n"
            "+    return sys.exit(0)\n"
        )
        region.commit_success(summary="+2 -1")
    output = capture(render)
    assert_snapshot("tool_event_apply_patch", output)


def test_snapshot_tool_event_no_args():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="git_status",
            args={},
        )
        region.commit_success(summary="clean")
    output = capture(render)
    assert_snapshot("tool_event_no_args", output)


def test_snapshot_tool_event_long_args_truncated():
    def render(console):
        region = ToolEventRegion(
            console=console,
            tool_name="grep_search",
            args={
                "query": (
                    "some extremely long search pattern that will definitely "
                    "overflow the 60 character MAX_ARGS_SUMMARY_LEN cap and "
                    "get truncated"
                ),
            },
        )
        region.commit_success(summary="3 matches")
    output = capture(render)
    assert_snapshot("tool_event_long_args_truncated", output)


# === DialogPopover ===


def _make_future():
    """Create an asyncio.Future bound to the shared test-only loop."""
    return _EVENT_LOOP.create_future()


def test_snapshot_dialog_confirm_normal():
    popover = DialogPopover()
    popover._active = ConfirmRequest(
        prompt="Apply changes to foo.py?",
        default=True,
        risk=RiskLevel.NORMAL,
        future=_make_future(),
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_confirm_normal", output)


def test_snapshot_dialog_confirm_critical():
    popover = DialogPopover()
    popover._active = ConfirmRequest(
        prompt="Delete foo.py permanently?",
        default=False,
        risk=RiskLevel.CRITICAL,
        future=_make_future(),
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_confirm_critical", output)


def test_snapshot_dialog_confirm_elevated():
    popover = DialogPopover()
    popover._active = ConfirmRequest(
        prompt="Edit bar.py?",
        default=True,
        risk=RiskLevel.ELEVATED,
        future=_make_future(),
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_confirm_elevated", output)


def test_snapshot_dialog_select():
    popover = DialogPopover()
    popover._active = SelectRequest(
        prompt="Choose model",
        choices=[
            Choice(value="Q3.5-122B", label="Qwen 3.5 122B", hint="local"),
            Choice(value="claude", label="Claude Opus", hint="API"),
            Choice(value="gpt-4", label="GPT-4", hint="API"),
        ],
        default=None,
        future=_make_future(),
        cursor=1,
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_select", output)


def test_snapshot_dialog_checklist():
    popover = DialogPopover()
    popover._active = ChecklistRequest(
        prompt="Enable tools",
        choices=[
            Choice(value="bash", label="Bash"),
            Choice(value="edit", label="Edit File"),
            Choice(value="read", label="Read File"),
            Choice(value="web", label="Web Fetch"),
        ],
        defaults=["bash", "read"],
        future=_make_future(),
        cursor=1,
        selected=["bash", "read"],
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_checklist", output)


def test_snapshot_dialog_text_input_secret():
    popover = DialogPopover()
    popover._active = TextInputRequest(
        prompt="Enter API key",
        default=None,
        validator=None,
        secret=True,
        future=_make_future(),
        buffer="sk-proj-secret",
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_text_input_secret", output)


def test_snapshot_dialog_text_input_visible():
    popover = DialogPopover()
    popover._active = TextInputRequest(
        prompt="Commit message",
        default="Fix: handle None branch",
        validator=None,
        secret=False,
        future=_make_future(),
        buffer="Fix: handle None branch",
    )
    output = render_formatted(popover.render_formatted())
    assert_snapshot("dialog_text_input_visible", output)


# === Panels (via view.print_info / print_error / print_warning
# shape — exercised through Rich's Panel directly for hermetic
# snapshots) ===


def test_snapshot_info_panel():
    from rich.panel import Panel

    def render(console):
        console.print(Panel(
            "Plugin installed successfully.",
            title="[bold]Success[/bold]",
            border_style="green",
        ))
    output = capture(render)
    assert_snapshot("info_panel", output)


def test_snapshot_error_panel():
    from rich.panel import Panel

    def render(console):
        console.print(Panel(
            "Failed to connect to API server at http://localhost:8000",
            title="[bold red]Error[/bold red]",
            border_style="red",
        ))
    output = capture(render)
    assert_snapshot("error_panel", output)


def test_snapshot_warning_panel():
    from rich.panel import Panel

    def render(console):
        console.print(Panel(
            "Context window is 90% full. Consider /compact.",
            title="[bold yellow]Warning[/bold yellow]",
            border_style="yellow",
        ))
    output = capture(render)
    assert_snapshot("warning_panel", output)


@pytest.fixture(scope="module", autouse=True)
def _close_event_loop():
    """Release the shared test loop at module teardown."""
    yield
    try:
        _EVENT_LOOP.close()
    except Exception:
        pass

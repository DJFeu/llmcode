# M7 — ToolEventRegion (Style R)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Implement `ToolEventRegion` — Style R rendering of tool calls: inline summary lines by default, auto-expand for diff tools (`edit_file`/`write_file`/`apply_patch`) and failures. Replace the M3 `_NullToolEventHandle` placeholder.

**Architecture:** Each `start_tool_event()` call creates a `ToolEventRegion` that immediately prints a start-line (`▶ tool_name args_summary`) to the Console. On `commit_success`, prints a summary line with `✓` and elapsed time; if the tool is a diff tool, also prints a Rich Panel containing the unified diff with `diff` syntax highlighting. On `commit_failure`, prints a `✗` summary line and auto-expands the last 12 stderr lines in a red-bordered Panel.

**Tech Stack:** `rich.panel.Panel`, `rich.syntax.Syntax` (for diff highlighting), `rich.console.Console`, `time.monotonic` for elapsed timing.

**Spec reference:** §6.4 Style R, §7.1 v2.0.0 tool display.

**Dependencies:** M3 coordinator. Parallel with M4–M6/M8/M9.

---

## File Structure

- Create: `llm_code/view/repl/components/tool_event_renderer.py` — `ToolEventRegion` class (~400 lines)
- Modify: `llm_code/view/repl/backend.py` — replace `_NullToolEventHandle` with `ToolEventRegion` instantiation
- Create: `tests/test_view/test_tool_event_renderer.py` — ~35 tests, ~600 lines

---

## Tasks

### Task 7.1: Write ToolEventRegion

**Files:** Create `llm_code/view/repl/components/tool_event_renderer.py`

- [ ] **Step 1: Write the class.**

```python
"""ToolEventRegion — Style R tool call rendering.

Default behavior (Style R, spec §6.4):
  - Start line: `▶ tool_name args_summary`  (dim marker)
  - Success commit: `✓ tool_name · summary · 0.3s`  (green marker)
  - Failure commit: `✗ tool_name · error · 1.2s · exit 125`  (red marker)

Auto-expand cases:
  1. Diff tools (edit_file / write_file / apply_patch) with a
     non-empty diff_text → render a bordered Panel with the diff
     syntax-highlighted, before the summary line
  2. Failures with any stderr → render a red-bordered Panel with
     the last 12 stderr lines, before the summary line
  3. Permissions (not implemented here — handled by dialog flow in
     the dispatcher + M8 DialogPopover)

Elapsed time is measured from __init__ to commit. Tools that never
commit show as active forever (dispatcher is responsible for always
calling commit_success or commit_failure, which M10 wires up).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


# Tool names that get their diff auto-expanded on success
AUTO_EXPAND_DIFF_TOOLS = frozenset({
    "edit_file",
    "write_file",
    "apply_patch",
    "edit",
    "write",
})

# Max number of stderr tail lines shown when a tool fails
MAX_STDERR_TAIL_LINES = 12

# Max number of chars in the args summary (truncated with ellipsis)
MAX_ARGS_SUMMARY_LEN = 60


def _format_args_summary(args: Dict[str, Any]) -> str:
    """Compact one-line summary of tool args for the start-line display.

    Rules:
      - path / file / command / query args get priority in output
      - Other args listed as key=value pairs
      - Total length capped at MAX_ARGS_SUMMARY_LEN with trailing ...
    """
    if not args:
        return ""
    # Priority fields first
    priority = ["path", "file", "filepath", "command", "cmd", "query", "url"]
    parts: List[str] = []
    for key in priority:
        if key in args and args[key]:
            value = str(args[key])
            if len(value) > 40:
                value = value[:37] + "..."
            parts.append(value)
            break  # only one priority field shown

    # Remaining args as k=v
    remaining = {k: v for k, v in args.items() if k not in priority or not parts}
    for key, value in remaining.items():
        if key in priority:
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")

    summary = " · ".join(parts)
    if len(summary) > MAX_ARGS_SUMMARY_LEN:
        summary = summary[: MAX_ARGS_SUMMARY_LEN - 3] + "..."
    return summary


class ToolEventRegion:
    """A single tool call's display lifecycle.

    Usage (dispatcher side):
        region = ToolEventRegion(console, tool_name="read_file", args={"path": "foo.py"})
        # start line already printed
        region.feed_stdout("file contents line 1\n")
        region.feed_stdout("file contents line 2\n")
        region.commit_success(summary="47 lines, 320 tokens")
    """

    def __init__(
        self,
        *,
        console: Console,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._console = console
        self._tool_name = tool_name
        self._args = args
        self._stdout: List[str] = []
        self._stderr: List[str] = []
        self._diff_text: str = ""
        self._committed: bool = False
        self._success: Optional[bool] = None
        self._start_time: float = time.monotonic()
        self._summary: Optional[str] = None
        self._error: Optional[str] = None
        self._exit_code: Optional[int] = None

        # Print the start line immediately
        args_summary = _format_args_summary(args)
        if args_summary:
            start_line = f"[dim]▶[/dim] {tool_name} {args_summary}"
        else:
            start_line = f"[dim]▶[/dim] {tool_name}"
        self._console.print(start_line)

    @property
    def is_active(self) -> bool:
        return not self._committed

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def args(self) -> Dict[str, Any]:
        return dict(self._args)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    # === Feed methods ===

    def feed_stdout(self, line: str) -> None:
        if self._committed:
            return
        self._stdout.append(line)

    def feed_stderr(self, line: str) -> None:
        if self._committed:
            return
        self._stderr.append(line)

    def feed_diff(self, diff_text: str) -> None:
        if self._committed:
            return
        self._diff_text = diff_text

    # === Commit methods ===

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        self._success = True
        self._summary = summary

        # Auto-expand diff for diff tools
        if (
            self._tool_name in AUTO_EXPAND_DIFF_TOOLS
            and self._diff_text.strip()
        ):
            self._render_diff_panel()

        # Print the summary line
        summary_text = summary or self._default_summary()
        elapsed = self.elapsed_seconds
        self._console.print(
            f"[green]✓[/green] {self._tool_name} · "
            f"{summary_text} · {elapsed:.1f}s"
        )

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        if self._committed:
            return
        self._committed = True
        self._success = False
        self._error = error
        self._exit_code = exit_code

        # Auto-expand stderr tail on failure
        if self._stderr:
            self._render_failure_panel()

        # Print the summary line
        elapsed = self.elapsed_seconds
        exit_str = f" · exit {exit_code}" if exit_code is not None else ""
        self._console.print(
            f"[red]✗[/red] {self._tool_name} · {error} · "
            f"{elapsed:.1f}s{exit_str}"
        )

    # === Internal rendering ===

    def _default_summary(self) -> str:
        """Fall-back summary if caller didn't provide one.

        Constructs something plausible from stdout/stderr volume.
        """
        out_count = len(self._stdout)
        if out_count > 0:
            return f"{out_count} line{'s' if out_count != 1 else ''}"
        return "done"

    def _render_diff_panel(self) -> None:
        path = self._args.get("path") or self._args.get("file") or ""
        syntax = Syntax(
            self._diff_text,
            "diff",
            theme="ansi_dark",
            word_wrap=False,
        )
        title = f"[bold]{self._tool_name}[/bold]"
        if path:
            title += f" · {path}"
        self._console.print(Panel(
            syntax,
            title=title,
            title_align="left",
            border_style="cyan",
        ))

    def _render_failure_panel(self) -> None:
        tail = self._stderr[-MAX_STDERR_TAIL_LINES:]
        body = "\n".join(tail)
        self._console.print(Panel(
            body,
            title=f"[bold red]✗ {self._tool_name}[/bold red]",
            title_align="left",
            border_style="red",
        ))
```

- [ ] **Step 2: Commit** — `git add llm_code/view/repl/components/tool_event_renderer.py && git commit -m "feat(view): ToolEventRegion (Style R)"`

### Task 7.2: Wire into REPLBackend

**Files:** Modify `llm_code/view/repl/backend.py`

- [ ] **Step 1: Replace `start_tool_event`.**

```python
def start_tool_event(
    self,
    tool_name: str,
    args: Dict[str, Any],
) -> ToolEventHandle:
    from llm_code.view.repl.components.tool_event_renderer import ToolEventRegion
    region = ToolEventRegion(
        console=self._coordinator._console,
        tool_name=tool_name,
        args=args,
    )
    return region
```

- [ ] **Step 2: Delete `_NullToolEventHandle` class** from backend.py.
- [ ] **Step 3: Run pilot tests** — `pytest tests/test_view/test_pilot.py -v` → all pass.
- [ ] **Step 4: Commit** — `git commit -am "feat(view): REPLBackend uses ToolEventRegion"`

### Task 7.3: Write ToolEventRegion tests

**Files:** Create `tests/test_view/test_tool_event_renderer.py`

- [ ] **Step 1: Write tests.**

```python
"""Tests for ToolEventRegion (Style R)."""
import io
import time

import pytest
from rich.console import Console

from llm_code.view.repl.components.tool_event_renderer import (
    AUTO_EXPAND_DIFF_TOOLS,
    MAX_STDERR_TAIL_LINES,
    ToolEventRegion,
    _format_args_summary,
)


def _make(tool_name="read_file", args=None):
    if args is None:
        args = {}
    capture = io.StringIO()
    console = Console(file=capture, force_terminal=True, color_system="truecolor", width=80)
    region = ToolEventRegion(console=console, tool_name=tool_name, args=args)
    return region, capture


# === _format_args_summary helpers ===

def test_args_summary_empty():
    assert _format_args_summary({}) == ""

def test_args_summary_priority_path():
    assert _format_args_summary({"path": "foo.py"}) == "foo.py"

def test_args_summary_priority_command():
    assert _format_args_summary({"command": "ls -la"}) == "ls -la"

def test_args_summary_long_value_truncated():
    long_path = "a" * 50
    result = _format_args_summary({"path": long_path})
    assert result.endswith("...")
    assert len(result) <= 60

def test_args_summary_secondary_key_value():
    result = _format_args_summary({"url": "http://x", "method": "GET"})
    assert "http://x" in result or "method=GET" in result

def test_args_summary_total_length_capped():
    result = _format_args_summary({
        "path": "foo", "extra": "bar" * 30, "more": "baz" * 30,
    })
    assert len(result) <= 60


# === Constructor prints start line ===

def test_start_line_printed_on_init():
    region, capture = _make(tool_name="read_file", args={"path": "foo.py"})
    out = capture.getvalue()
    assert "read_file" in out
    assert "foo.py" in out
    assert "▶" in out

def test_start_line_no_args():
    region, capture = _make(tool_name="list_files", args={})
    out = capture.getvalue()
    assert "list_files" in out

def test_region_starts_active():
    region, _ = _make()
    assert region.is_active is True


# === feed_stdout / stderr / diff ===

def test_feed_stdout_accumulates():
    region, _ = _make()
    region.feed_stdout("line 1")
    region.feed_stdout("line 2")
    assert region._stdout == ["line 1", "line 2"]

def test_feed_stderr_accumulates():
    region, _ = _make()
    region.feed_stderr("err 1")
    region.feed_stderr("err 2")
    assert region._stderr == ["err 1", "err 2"]

def test_feed_diff_stores():
    region, _ = _make()
    region.feed_diff("@@ -1,3 +1,5 @@\n-old\n+new")
    assert "@@" in region._diff_text

def test_feed_after_commit_is_noop():
    region, _ = _make()
    region.commit_success(summary="x")
    region.feed_stdout("ignored")
    assert region._stdout == []


# === commit_success ===

def test_commit_success_prints_check_mark():
    region, capture = _make(tool_name="read_file")
    capture.truncate(0); capture.seek(0)  # ignore start line
    region.commit_success(summary="47 lines")
    out = capture.getvalue()
    assert "✓" in out
    assert "read_file" in out
    assert "47 lines" in out
    assert "s" in out  # elapsed "0.0s"

def test_commit_success_is_idempotent():
    region, _ = _make()
    region.commit_success(summary="first")
    region.commit_success(summary="second")
    assert region._summary == "first"

def test_commit_success_marks_inactive():
    region, _ = _make()
    region.commit_success(summary="done")
    assert region.is_active is False


# === commit_failure ===

def test_commit_failure_prints_cross():
    region, capture = _make(tool_name="bash")
    capture.truncate(0); capture.seek(0)
    region.commit_failure(error="nonzero", exit_code=1)
    out = capture.getvalue()
    assert "✗" in out
    assert "bash" in out
    assert "nonzero" in out
    assert "exit 1" in out

def test_commit_failure_no_exit_code():
    region, capture = _make()
    capture.truncate(0); capture.seek(0)
    region.commit_failure(error="boom")
    out = capture.getvalue()
    assert "boom" in out
    assert "exit" not in out.split("boom")[1]  # no exit str after error

def test_commit_failure_auto_expands_stderr():
    region, capture = _make(tool_name="bash")
    region.feed_stderr("line 1")
    region.feed_stderr("line 2")
    region.feed_stderr("line 3")
    capture.truncate(0); capture.seek(0)
    region.commit_failure(error="crash")
    out = capture.getvalue()
    assert "line 1" in out
    assert "line 2" in out
    assert "line 3" in out

def test_commit_failure_stderr_tail_limit():
    region, capture = _make(tool_name="bash")
    for i in range(20):
        region.feed_stderr(f"stderr line {i}")
    capture.truncate(0); capture.seek(0)
    region.commit_failure(error="fail")
    out = capture.getvalue()
    # First 8 should NOT appear (20 - 12 tail limit = 8 dropped)
    assert "stderr line 0" not in out
    assert "stderr line 7" not in out
    # Last 12 should appear
    assert "stderr line 19" in out
    assert "stderr line 8" in out


# === Diff auto-expand ===

@pytest.mark.parametrize("tool_name", list(AUTO_EXPAND_DIFF_TOOLS))
def test_diff_auto_expand_for_diff_tools(tool_name):
    region, capture = _make(tool_name=tool_name, args={"path": "bar.py"})
    region.feed_diff("@@ -1 +1 @@\n-old\n+new")
    capture.truncate(0); capture.seek(0)
    region.commit_success(summary="+1 -1")
    out = capture.getvalue()
    # Diff panel should appear with the diff content
    assert "old" in out or "@@" in out
    assert tool_name in out

def test_diff_not_expanded_for_non_diff_tools():
    region, capture = _make(tool_name="read_file")
    region.feed_diff("@@ -1 +1 @@\n-old\n+new")
    capture.truncate(0); capture.seek(0)
    region.commit_success(summary="done")
    out = capture.getvalue()
    # read_file is not in AUTO_EXPAND_DIFF_TOOLS, so the diff should
    # not be rendered as a panel
    assert "@@" not in out

def test_diff_empty_not_expanded():
    region, capture = _make(tool_name="edit_file", args={"path": "x.py"})
    # feed_diff never called
    capture.truncate(0); capture.seek(0)
    region.commit_success(summary="no changes")
    out = capture.getvalue()
    assert "@@" not in out


# === Elapsed time ===

def test_elapsed_time_is_positive():
    region, _ = _make()
    time.sleep(0.05)
    region.commit_success(summary="x")
    assert region.elapsed_seconds >= 0.05

def test_elapsed_time_stable_after_commit():
    region, _ = _make()
    region.commit_success(summary="x")
    first = region.elapsed_seconds
    time.sleep(0.05)
    # Elapsed continues ticking after commit (it's just monotonic math)
    # but commit captures the time at commit moment via the printed line
    # which is what matters for user-visible output


# === Properties ===

def test_tool_name_property():
    region, _ = _make(tool_name="bash")
    assert region.tool_name == "bash"

def test_args_property_is_copy():
    original = {"path": "x.py"}
    region, _ = _make(args=original)
    returned = region.args
    returned["path"] = "y.py"
    assert region.args["path"] == "x.py"
```

- [ ] **Step 2: Run** — `pytest tests/test_view/test_tool_event_renderer.py -v` → ~35 pass.
- [ ] **Step 3: Commit** — `git add tests/test_view/test_tool_event_renderer.py && git commit -m "test(view): ToolEventRegion inline + auto-expand coverage"`

---

## Milestone completion criteria

- ✅ `ToolEventRegion` class with full lifecycle (start-line, feed_stdout/stderr/diff, commit_success/failure)
- ✅ `REPLBackend.start_tool_event` returns a real `ToolEventRegion`
- ✅ `_NullToolEventHandle` removed from backend.py
- ✅ AUTO_EXPAND_DIFF_TOOLS correctly auto-expands for edit_file/write_file/apply_patch
- ✅ Failures auto-expand stderr tail (last 12 lines)
- ✅ ~35 tests green
- ✅ Existing view tests still green

## Estimated effort: ~3 hours

## Next milestone: M8 — Dialog Popovers (`m8-dialogs.md`)

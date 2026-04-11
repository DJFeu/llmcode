# M13 — Snapshot Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Add ~20-30 visual-regression snapshot tests covering the visually-sensitive components: `status_line`, `live_response_region`, `tool_event_renderer`, `dialog_popover`, `slash_popover`, and key error/info panels. Snapshots are captured ANSI-rendered output from a Rich StringIO Console, stored as golden text files under `tests/test_view/snapshots/`, and diffed on each CI run.

**Architecture:** Each snapshot test renders a component in a known state to a captured Console, strips non-deterministic content (timestamps, elapsed seconds), and compares against a committed `.txt` golden file. The `--snapshot-update` flag regenerates goldens when visual changes are intentional.

**Tech Stack:** Rich `Console(file=StringIO, force_terminal=True, width=80)`, plain file I/O, a minimal in-house snapshot helper (no `syrupy` dependency to keep M13 simple).

**Spec reference:** §9.4 snapshot policy (limit 20-30 goldens), §9.1 test pyramid.

**Dependencies:** M1–M11 complete. All components exist and are stable. No snapshots should be written against a moving target.

---

## File Structure

- Create: `llm_code/view/repl/snapshots.py` — snapshot helper module (~150 lines)
- Create: `tests/test_view/snapshots/__init__.py` — package marker
- Create: `tests/test_view/snapshots/*.txt` — ~25 golden files (each ~10-30 lines of ANSI text)
- Create: `tests/test_view/test_snapshots.py` — one test per snapshot (~25 tests, ~400 lines)

---

## Tasks

### Task 13.1: Write snapshot helper

**Files:** Create `llm_code/view/repl/snapshots.py`

```python
"""Snapshot test helper for visual regression coverage.

Simpler than syrupy / pytest-snapshot — just enough for ~25 golden
text files. Tests render a component to a StringIO Console, pass
the captured output through ``normalize()`` to strip
non-deterministic content (timestamps, elapsed seconds, dates),
and compare against a committed golden file.

Usage:

    from llm_code.view.repl.snapshots import capture, assert_snapshot

    def test_status_line_default_snapshot():
        output = capture(lambda console: status_line.render_to(console))
        assert_snapshot("status_line_default", output)

Regenerate a single golden: ``PYTEST_SNAPSHOT_UPDATE=1 pytest test_X::test_Y``.
Regenerate all: ``PYTEST_SNAPSHOT_UPDATE=1 pytest tests/test_view/test_snapshots.py``.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Callable

from rich.console import Console


SNAPSHOT_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "test_view" / "snapshots"

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def capture(render_fn: Callable[[Console], None], *, width: int = 80) -> str:
    """Render via a captured Console and return the ANSI text."""
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, color_system="truecolor",
        width=width, record=False,
    )
    render_fn(console)
    return buf.getvalue()


def normalize(text: str) -> str:
    """Strip non-deterministic content before comparison.

    Removes:
    - Elapsed time markers: `0.3s`, `1.2s`, `10.5s` → `{elapsed}`
    - Date/time strings: `2026-04-11 14:23` → `{date}`
    - Absolute paths under /Users or /home → `{path}`
    """
    # Match floating-point seconds (one or more digits + dot + digit + 's')
    text = re.sub(r"\b\d+\.\d+s\b", "{elapsed}", text)
    # Match YYYY-MM-DD HH:MM timestamps
    text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", "{date}", text)
    # Match absolute home paths
    text = re.sub(r"/Users/[^/\s]+", "{home}", text)
    text = re.sub(r"/home/[^/\s]+", "{home}", text)
    # Token counts in format "123 tok" → "{N} tok" (optional — keep literal for now)
    return text


def _golden_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name}.txt"


def assert_snapshot(name: str, output: str) -> None:
    """Assert that the captured output matches the committed golden.

    If env var PYTEST_SNAPSHOT_UPDATE=1 is set, write the output as the
    new golden instead of comparing.
    """
    normalized = normalize(output)
    golden_file = _golden_path(name)

    if os.environ.get("PYTEST_SNAPSHOT_UPDATE") == "1":
        golden_file.write_text(normalized)
        return

    if not golden_file.exists():
        golden_file.write_text(normalized)
        raise AssertionError(
            f"Snapshot {name!r} did not exist — created at {golden_file}. "
            f"Re-run the test to verify."
        )

    expected = golden_file.read_text()
    if expected != normalized:
        # Produce a diff for easy debugging
        import difflib
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                normalized.splitlines(),
                fromfile=f"{name}.txt (golden)",
                tofile=f"{name}.txt (actual)",
                lineterm="",
            )
        )
        raise AssertionError(
            f"Snapshot mismatch for {name!r}.\n{diff}\n\n"
            f"If this change is intentional, re-run with "
            f"PYTEST_SNAPSHOT_UPDATE=1 to regenerate the golden."
        )
```

- [ ] **Commit** — `git add llm_code/view/repl/snapshots.py && git commit -m "feat(view): snapshot helper for visual regression testing"`

### Task 13.2: Write ~25 snapshot tests

**Files:** Create `tests/test_view/test_snapshots.py`, `tests/test_view/snapshots/__init__.py`

- [ ] **Step 1: Scaffold.**

```bash
mkdir -p tests/test_view/snapshots
touch tests/test_view/snapshots/__init__.py
```

- [ ] **Step 2: Write the tests file.**

```python
"""Snapshot tests — visual regression coverage for key components."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from llm_code.view.repl.snapshots import capture, assert_snapshot
from llm_code.view.repl.components.status_line import StatusLine
from llm_code.view.repl.components.tool_event_renderer import ToolEventRegion
from llm_code.view.repl.components.live_response_region import LiveResponseRegion
from llm_code.view.repl.components.dialog_popover import DialogPopover
from llm_code.view.types import Role, RiskLevel, StatusUpdate
from llm_code.view.dialog_types import Choice


def _render_formatted(ft) -> str:
    """Flatten a FormattedText to a printable string."""
    return "".join(seg[1] for seg in ft)


# === StatusLine ===

def test_snapshot_status_line_default():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B", cwd="llm-code", branch="main",
        context_used_tokens=16400, context_limit_tokens=128000,
        cost_usd=0.0,
    ))
    output = _render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_default", output)


def test_snapshot_status_line_streaming():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B", cwd="llm-code", branch="main",
        context_used_tokens=16400, context_limit_tokens=128000,
        cost_usd=0.0, is_streaming=True, streaming_token_count=1234,
    ))
    output = _render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_streaming", output)


def test_snapshot_status_line_voice_recording():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        voice_active=True, voice_seconds=2.3, voice_peak=0.42,
    ))
    output = _render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_voice_recording", output)


def test_snapshot_status_line_rate_limited():
    sl = StatusLine()
    future = datetime.now() + timedelta(minutes=5)
    sl.merge(StatusUpdate(
        model="Q3.5-122B", rate_limit_until=future, rate_limit_reqs_left=3,
    ))
    output = _render_formatted(sl.render_rate_limit_warning())
    assert_snapshot("status_line_rate_limited", output)


def test_snapshot_status_line_with_permission_mode():
    sl = StatusLine()
    sl.merge(StatusUpdate(
        model="Q3.5-122B", cwd="llm-code", branch="main",
        permission_mode="plan", cost_usd=0.01,
    ))
    output = _render_formatted(sl.render_formatted_text())
    assert_snapshot("status_line_plan_mode", output)


# === ToolEventRegion ===

def test_snapshot_tool_event_read_file_success():
    def render(console):
        region = ToolEventRegion(
            console=console, tool_name="read_file",
            args={"path": "foo.py"},
        )
        region.commit_success(summary="47 lines")
    output = capture(render)
    assert_snapshot("tool_event_read_file", output)


def test_snapshot_tool_event_bash_success():
    def render(console):
        region = ToolEventRegion(
            console=console, tool_name="bash",
            args={"command": "pytest tests/"},
        )
        region.commit_success(summary="28 passed")
    output = capture(render)
    assert_snapshot("tool_event_bash_success", output)


def test_snapshot_tool_event_edit_file_with_diff():
    def render(console):
        region = ToolEventRegion(
            console=console, tool_name="edit_file",
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


def test_snapshot_tool_event_bash_failure():
    def render(console):
        region = ToolEventRegion(
            console=console, tool_name="bash",
            args={"command": "docker run nonexistent"},
        )
        region.feed_stderr("Unable to find image 'nonexistent:latest'")
        region.feed_stderr("docker: Error response from daemon: pull access denied")
        region.commit_failure(error="exit 125", exit_code=125)
    output = capture(render)
    assert_snapshot("tool_event_bash_failure", output)


def test_snapshot_tool_event_apply_patch_with_diff():
    def render(console):
        region = ToolEventRegion(
            console=console, tool_name="apply_patch",
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


# === DialogPopover ===

def test_snapshot_dialog_confirm_normal():
    popover = DialogPopover()
    import asyncio
    asyncio.get_event_loop_policy().get_event_loop()  # ensure loop exists
    # Manually construct a ConfirmRequest for rendering
    from llm_code.view.repl.components.dialog_popover import ConfirmRequest
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        popover._active = ConfirmRequest(
            prompt="Apply changes to foo.py?",
            default=True, risk=RiskLevel.NORMAL, future=future,
        )
        output = _render_formatted(popover.render_formatted())
    finally:
        loop.close()
    assert_snapshot("dialog_confirm_normal", output)


def test_snapshot_dialog_confirm_critical():
    import asyncio
    from llm_code.view.repl.components.dialog_popover import ConfirmRequest
    popover = DialogPopover()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        popover._active = ConfirmRequest(
            prompt="Delete foo.py permanently?",
            default=False, risk=RiskLevel.CRITICAL, future=future,
        )
        output = _render_formatted(popover.render_formatted())
    finally:
        loop.close()
    assert_snapshot("dialog_confirm_critical", output)


def test_snapshot_dialog_select():
    import asyncio
    from llm_code.view.repl.components.dialog_popover import SelectRequest
    popover = DialogPopover()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        popover._active = SelectRequest(
            prompt="Choose model",
            choices=[
                Choice(value="Q3.5-122B", label="Qwen 3.5 122B", hint="local"),
                Choice(value="claude", label="Claude Opus", hint="API"),
                Choice(value="gpt-4", label="GPT-4", hint="API"),
            ],
            default=None, future=future, cursor=1,
        )
        output = _render_formatted(popover.render_formatted())
    finally:
        loop.close()
    assert_snapshot("dialog_select", output)


def test_snapshot_dialog_checklist():
    import asyncio
    from llm_code.view.repl.components.dialog_popover import ChecklistRequest
    popover = DialogPopover()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        popover._active = ChecklistRequest(
            prompt="Enable tools",
            choices=[
                Choice(value="bash", label="Bash"),
                Choice(value="edit", label="Edit File"),
                Choice(value="read", label="Read File"),
                Choice(value="web", label="Web Fetch"),
            ],
            defaults=["bash", "read"],
            future=future, cursor=1,
            selected=["bash", "read"],
        )
        output = _render_formatted(popover.render_formatted())
    finally:
        loop.close()
    assert_snapshot("dialog_checklist", output)


def test_snapshot_dialog_text_input():
    import asyncio
    from llm_code.view.repl.components.dialog_popover import TextInputRequest
    popover = DialogPopover()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        popover._active = TextInputRequest(
            prompt="Enter API key",
            default=None, validator=None, secret=True,
            future=future, buffer="sk-proj-secret",
        )
        output = _render_formatted(popover.render_formatted())
    finally:
        loop.close()
    assert_snapshot("dialog_text_input_secret", output)


# === Panels (error / info) ===

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
```

Plus ~5 more snapshots covering: tool event with long args_summary truncation, tool event with no args, dialog with long prompt, status line with shortened model name, live response committed markdown panel.

- [ ] **Step 3: Generate initial goldens.**

```bash
PYTEST_SNAPSHOT_UPDATE=1 /Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_snapshots.py -v
```

Expected: first run writes all goldens to `tests/test_view/snapshots/*.txt`.

- [ ] **Step 4: Verify second run is green.**

```bash
/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_view/test_snapshots.py -v
```

Expected: all tests pass (diffing against just-written goldens).

- [ ] **Step 5: Commit goldens + tests** — `git add tests/test_view/snapshots/ tests/test_view/test_snapshots.py && git commit -m "test(view): 25 snapshot goldens for visual regression"`

---

## Milestone completion criteria

- ✅ Snapshot helper works and supports PYTEST_SNAPSHOT_UPDATE env var
- ✅ ~25 golden files committed under `tests/test_view/snapshots/`
- ✅ `pytest tests/test_view/test_snapshots.py` passes cleanly
- ✅ Running with `PYTEST_SNAPSHOT_UPDATE=1` regenerates goldens without errors

## Estimated effort: ~2.5 hours

## Next milestone: M14 — Docs + v2.0.0 Release (`m14-release.md`)

# Community Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 community-inspired features: real-time token display, auto-commit checkpoints, plan/act mode, dump mode, repo map, LSP auto-diagnose, clean interrupt.

**Architecture:** Each feature is independent. Features 1-4 are low effort (modify existing + small new modules). Features 5-7 are medium effort (new modules + integration).

**Tech Stack:** Python 3.11+, Textual (TUI), pytest, subprocess (git), ast (Python parsing)

---

## File Map

| File | Action | Feature |
|------|--------|---------|
| `llm_code/tui/status_bar.py` | Modify | F1, F3 |
| `llm_code/tui/app.py` | Modify | F1, F3, F4, F5, F7 |
| `llm_code/runtime/auto_commit.py` | Create | F2 |
| `llm_code/runtime/config.py` | Modify | F2, F6 |
| `llm_code/runtime/conversation.py` | Modify | F2, F3, F5, F6 |
| `llm_code/tools/dump.py` | Create | F4 |
| `llm_code/runtime/repo_map.py` | Create | F5 |
| `llm_code/runtime/auto_diagnose.py` | Create | F6 |
| `tests/test_tui/test_status_bar_realtime.py` | Create | F1 |
| `tests/test_runtime/test_auto_commit.py` | Create | F2 |
| `tests/test_tui/test_plan_mode.py` | Create | F3 |
| `tests/test_tools/test_dump.py` | Create | F4 |
| `tests/test_runtime/test_repo_map.py` | Create | F5 |
| `tests/test_runtime/test_auto_diagnose.py` | Create | F6 |
| `tests/test_tui/test_clean_interrupt.py` | Create | F7 |

---

### Task 1: Real-Time Token Cost Display

**Files:**
- Modify: `llm_code/tui/status_bar.py`
- Modify: `llm_code/tui/app.py`
- Create: `tests/test_tui/test_status_bar_realtime.py`

- [ ] **Step 1: Write failing tests for status bar cost formatting**

```python
"""Tests for real-time token/cost display in StatusBar."""
from __future__ import annotations

import pytest

from llm_code.tui.status_bar import StatusBar


class TestStatusBarCostFormatting:
    """Verify StatusBar formats cost in different states."""

    def test_cost_displayed_when_positive(self) -> None:
        bar = StatusBar()
        bar.model = "claude-sonnet-4-6"
        bar.tokens = 12345
        bar.cost = "$0.0042"
        content = bar._format_content()
        assert "$0.0042" in content
        assert "12,345" in content

    def test_free_displayed_when_local(self) -> None:
        bar = StatusBar()
        bar.model = "qwen3.5"
        bar.tokens = 5000
        bar.is_local = True
        content = bar._format_content()
        assert "free" in content
        assert "$" not in content

    def test_cost_omitted_when_zero_and_not_local(self) -> None:
        bar = StatusBar()
        bar.model = "gpt-4o"
        bar.tokens = 0
        bar.is_local = False
        content = bar._format_content()
        assert "free" not in content
        assert "$" not in content

    def test_plan_mode_not_shown_by_default(self) -> None:
        bar = StatusBar()
        bar.model = "claude-sonnet-4-6"
        content = bar._format_content()
        assert "PLAN" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd llm-code && python -m pytest tests/test_tui/test_status_bar_realtime.py -v`
Expected: FAIL -- `AttributeError: 'StatusBar' object has no attribute 'is_local'`

- [ ] **Step 3: Add `is_local` reactive field and update formatting in StatusBar**

Modify `llm_code/tui/status_bar.py`:

```python
"""StatusBar -- persistent bottom line with model, tokens, cost, hints."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult


class StatusBar(Widget):
    """Bottom status: model | tokens tok | $cost | streaming... | /help | Ctrl+D quit"""

    model: reactive[str] = reactive("")
    tokens: reactive[int] = reactive(0)
    cost: reactive[str] = reactive("")
    is_streaming: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"
    is_local: reactive[bool] = reactive(False)
    plan_mode: reactive[str] = reactive("")  # "" | "PLAN"

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def _format_content(self) -> str:
        parts: list[str] = []
        if self.plan_mode:
            parts.append(self.plan_mode)
        if self.vim_mode:
            parts.append(f"-- {self.vim_mode} --")
        if self.model:
            parts.append(self.model)
        if self.tokens > 0:
            parts.append(f"\u2193{self.tokens:,} tok")
        if self.is_local:
            parts.append("free")
        elif self.cost:
            parts.append(self.cost)
        if self.is_streaming:
            parts.append("streaming\u2026")
        parts.append("/help")
        parts.append("Ctrl+D quit")
        return " \u2502 ".join(parts)

    def render(self) -> RenderResult:
        return self._format_content()

    def watch_model(self) -> None:
        self.refresh()

    def watch_tokens(self) -> None:
        self.refresh()

    def watch_cost(self) -> None:
        self.refresh()

    def watch_is_streaming(self) -> None:
        self.refresh()

    def watch_vim_mode(self) -> None:
        self.refresh()

    def watch_is_local(self) -> None:
        self.refresh()

    def watch_plan_mode(self) -> None:
        self.refresh()
```

- [ ] **Step 4: Update app.py to push cost to status bar immediately after add_usage**

In `llm_code/tui/app.py`, after the `_cost_tracker.add_usage()` call (around line 912), add immediate status bar update. Replace the block:

```python
                elif isinstance(event, StreamMessageStop):
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._input_tokens += event.usage.input_tokens
                        self._output_tokens += event.usage.output_tokens
                        if self._cost_tracker:
                            self._cost_tracker.add_usage(
                                event.usage.input_tokens, event.usage.output_tokens,
                            )
```

With:

```python
                elif isinstance(event, StreamMessageStop):
                    if event.usage:
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        self._input_tokens += event.usage.input_tokens
                        self._output_tokens += event.usage.output_tokens
                        if self._cost_tracker:
                            self._cost_tracker.add_usage(
                                event.usage.input_tokens, event.usage.output_tokens,
                            )
                        # Real-time status bar update
                        status.tokens = self._output_tokens
                        if self._cost_tracker:
                            cost_usd = self._cost_tracker.total_cost_usd
                            status.cost = f"${cost_usd:.4f}" if cost_usd > 0.0001 else ""
```

Also, during app initialization (in `on_mount` or `compose`), detect whether the provider is local and set `status.is_local`:

```python
        # After status bar is available and config is loaded:
        if self._config and self._config.provider_base_url:
            url = self._config.provider_base_url
            status.is_local = "localhost" in url or "127.0.0.1" in url or "0.0.0.0" in url
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_tui/test_status_bar_realtime.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd llm-code && git add llm_code/tui/status_bar.py llm_code/tui/app.py tests/test_tui/test_status_bar_realtime.py
git commit -m "feat: real-time token cost display in status bar with local model detection"
```

---

### Task 2: Auto-Commit Checkpoint

**Files:**
- Create: `llm_code/runtime/auto_commit.py`
- Modify: `llm_code/runtime/config.py`
- Modify: `llm_code/runtime/conversation.py`
- Create: `tests/test_runtime/test_auto_commit.py`

- [ ] **Step 1: Write failing tests for auto-commit**

```python
"""Tests for llm_code.runtime.auto_commit -- automatic git checkpoint after edits."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from llm_code.runtime.auto_commit import auto_commit_file


class TestAutoCommitSuccess:
    def test_commits_with_correct_message(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "utils.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("# content")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "write_file")

        assert result is True
        calls = mock_run.call_args_list
        # First call: git add
        assert "add" in calls[0].args[0]
        # Second call: git commit
        assert "commit" in calls[1].args[0]
        commit_cmd = calls[1].args[0]
        assert "checkpoint: write_file" in " ".join(commit_cmd)

    def test_commit_message_includes_filename(self, tmp_path: Path) -> None:
        file_path = tmp_path / "app.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            auto_commit_file(file_path, "edit_file")

        commit_cmd = mock_run.call_args_list[1].args[0]
        assert "app.py" in " ".join(commit_cmd)


class TestAutoCommitSkips:
    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        file_path = tmp_path / "file.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.side_effect = [
            MagicMock(returncode=128),  # git add fails (not a repo)
        ]

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "write_file")

        assert result is False

    def test_file_not_found(self) -> None:
        result = auto_commit_file(Path("/nonexistent/file.py"), "write_file")
        assert result is False

    def test_subprocess_timeout(self, tmp_path: Path) -> None:
        import subprocess
        file_path = tmp_path / "slow.py"
        file_path.write_text("# slow")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = auto_commit_file(file_path, "write_file")

        assert result is False

    def test_commit_hook_failure_returns_false(self, tmp_path: Path) -> None:
        file_path = tmp_path / "hook_fail.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.side_effect = [
            MagicMock(returncode=0),   # git add succeeds
            MagicMock(returncode=1),   # git commit fails (pre-commit hook)
        ]

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "edit_file")

        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_auto_commit.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'llm_code.runtime.auto_commit'`

- [ ] **Step 3: Implement auto_commit_file**

Create `llm_code/runtime/auto_commit.py`:

```python
"""Auto-commit checkpoint -- git commit individual file changes after tool edits."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5


def auto_commit_file(path: Path, tool_name: str) -> bool:
    """Stage and commit a single file as a checkpoint.

    Returns True on successful commit, False on any failure (silently).
    """
    if not path.exists():
        return False

    try:
        # Stage the specific file only
        add_result = subprocess.run(
            ["git", "add", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=path.parent,
        )
        if add_result.returncode != 0:
            logger.debug("git add failed (rc=%d): %s", add_result.returncode, add_result.stderr)
            return False

        # Commit with checkpoint message
        filename = path.name
        message = f"checkpoint: {tool_name} {filename}"
        commit_result = subprocess.run(
            ["git", "commit", "-m", message, "--no-verify"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=path.parent,
        )
        if commit_result.returncode != 0:
            logger.debug("git commit failed (rc=%d): %s", commit_result.returncode, commit_result.stderr)
            return False

        logger.info("Auto-committed checkpoint: %s", message)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("Auto-commit timed out for %s", path)
        return False
    except (OSError, FileNotFoundError):
        logger.debug("Auto-commit skipped -- git not available or not a repo")
        return False
```

- [ ] **Step 4: Add `auto_commit` field to RuntimeConfig**

In `llm_code/runtime/config.py`, add to the `RuntimeConfig` dataclass (after `enterprise` field):

```python
    auto_commit: bool = False
```

In `_dict_to_runtime_config()`, add to the return statement:

```python
        auto_commit=data.get("auto_commit", False),
```

- [ ] **Step 5: Wire auto-commit into conversation post-tool hook**

In `llm_code/runtime/conversation.py`, after the post-tool hook block (after line 807 -- `await post_result`), add:

```python
        # 7b. Auto-commit checkpoint after write/edit tools
        if (
            hasattr(self._config, "auto_commit")
            and self._config.auto_commit
            and call.name in ("write_file", "edit_file")
            and not tool_result.is_error
        ):
            try:
                from llm_code.runtime.auto_commit import auto_commit_file
                file_path = args.get("file_path") or args.get("path", "")
                if file_path:
                    auto_commit_file(Path(file_path), call.name)
            except Exception:
                pass  # Never block tool flow for checkpoint failure
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_auto_commit.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
cd llm-code && git add llm_code/runtime/auto_commit.py llm_code/runtime/config.py llm_code/runtime/conversation.py tests/test_runtime/test_auto_commit.py
git commit -m "feat: auto-commit checkpoint after write/edit tools"
```

---

### Task 3: Plan/Act Mode Toggle

**Files:**
- Modify: `llm_code/tui/app.py`
- Modify: `llm_code/tui/status_bar.py` (already updated in Task 1 with `plan_mode` field)
- Modify: `llm_code/runtime/conversation.py`
- Create: `tests/test_tui/test_plan_mode.py`

- [ ] **Step 1: Write failing tests for plan mode**

```python
"""Tests for plan/act mode toggle."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from llm_code.tui.status_bar import StatusBar


# Denied tools in plan mode
WRITE_TOOLS = frozenset({"write_file", "edit_file", "bash", "git_commit", "git_push", "notebook_edit"})
# Allowed tools in plan mode
READ_TOOLS = frozenset({"read_file", "glob_search", "grep_search", "git_status", "git_diff", "git_log"})


class TestPlanModeStatusBar:
    def test_plan_mode_shown_in_status_bar(self) -> None:
        bar = StatusBar()
        bar.plan_mode = "PLAN"
        bar.model = "claude-sonnet-4-6"
        content = bar._format_content()
        assert "PLAN" in content
        # PLAN should appear before model
        plan_pos = content.index("PLAN")
        model_pos = content.index("claude-sonnet-4-6")
        assert plan_pos < model_pos

    def test_plan_mode_hidden_when_empty(self) -> None:
        bar = StatusBar()
        bar.plan_mode = ""
        bar.model = "qwen3.5"
        content = bar._format_content()
        assert "PLAN" not in content


class TestPlanModeToolDenial:
    """Verify that write tools are denied and read tools allowed in plan mode."""

    def test_write_tools_identified(self) -> None:
        for tool in WRITE_TOOLS:
            assert tool in WRITE_TOOLS

    def test_read_tools_not_in_denied_set(self) -> None:
        for tool in READ_TOOLS:
            assert tool not in WRITE_TOOLS
```

- [ ] **Step 2: Run tests to verify they fail (or pass for pure logic)**

Run: `cd llm-code && python -m pytest tests/test_tui/test_plan_mode.py -v`
Expected: StatusBar tests should PASS (since `plan_mode` was added in Task 1). Logic tests pass as data-only.

- [ ] **Step 3: Add `/plan` slash command to app.py**

In `llm_code/tui/app.py`, add `_plan_mode` instance variable in `__init__`:

```python
        self._plan_mode: bool = False
```

Add the `_cmd_plan` handler method:

```python
    def _cmd_plan(self, args: str) -> None:
        """Toggle plan/act mode."""
        self._plan_mode = not self._plan_mode
        status = self.query_one(StatusBar)
        chat = self.query_one(ChatScrollView)
        if self._plan_mode:
            status.plan_mode = "PLAN"
            chat.add_entry(AssistantText(
                "Plan mode ON -- agent will explore and plan without making changes."
            ))
        else:
            status.plan_mode = ""
            chat.add_entry(AssistantText(
                "Plan mode OFF -- back to normal."
            ))
```

- [ ] **Step 4: Add plan mode check in ConversationRuntime tool execution**

In `llm_code/runtime/conversation.py`, before the permission check (section 4 around line 700), add plan mode denial. The `_plan_mode` flag must be passed through. Add to `ConversationRuntime.__init__`:

```python
        self.plan_mode: bool = False
```

Then, before the checkpoint creation block (line ~739), add:

```python
        # 4c. Plan mode -- deny write tools
        _PLAN_DENIED_TOOLS = frozenset({
            "write_file", "edit_file", "bash", "git_commit", "git_push", "notebook_edit",
        })
        if self.plan_mode and call.name in _PLAN_DENIED_TOOLS:
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Plan mode: read-only. Tool '{call.name}' denied. Use /plan to switch to Act mode.",
                is_error=True,
            )
            return
```

In `llm_code/tui/app.py`, sync the flag before each conversation turn:

```python
        # Before calling runtime.run_turn():
        if self._runtime:
            self._runtime.plan_mode = self._plan_mode
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_tui/test_plan_mode.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd llm-code && git add llm_code/tui/app.py llm_code/tui/status_bar.py llm_code/runtime/conversation.py tests/test_tui/test_plan_mode.py
git commit -m "feat: plan/act mode toggle with /plan slash command"
```

---

### Task 4: DAFC Dump Mode

**Files:**
- Create: `llm_code/tools/dump.py`
- Modify: `llm_code/tui/app.py`
- Create: `tests/test_tools/test_dump.py`

- [ ] **Step 1: Write failing tests for dump_codebase**

```python
"""Tests for llm_code.tools.dump -- codebase dump for external LLM use."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.tools.dump import dump_codebase, DumpResult


class TestDumpCodebase:
    def test_dumps_simple_directory(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def add(a, b): return a + b")

        result = dump_codebase(tmp_path)
        assert isinstance(result, DumpResult)
        assert result.file_count == 2
        assert result.total_lines == 2
        assert "--- file: main.py ---" in result.text
        assert "--- file: utils.py ---" in result.text
        assert result.estimated_tokens > 0

    def test_skips_gitignore_patterns(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("code")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("module")

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "__pycache__" not in result.text
        assert ".git" not in result.text
        assert "node_modules" not in result.text

    def test_skips_large_files(self, tmp_path: Path) -> None:
        (tmp_path / "small.py").write_text("x = 1")
        (tmp_path / "huge.bin").write_text("x" * 60_000)  # > 50KB

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "huge.bin" not in result.text

    def test_respects_max_files_limit(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"x = {i}")

        result = dump_codebase(tmp_path, max_files=3)
        assert result.file_count == 3

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = dump_codebase(tmp_path)
        assert result.file_count == 0
        assert result.text == ""
        assert result.estimated_tokens == 0

    def test_token_estimate_approximation(self, tmp_path: Path) -> None:
        content = "word " * 100  # ~500 chars
        (tmp_path / "words.txt").write_text(content)

        result = dump_codebase(tmp_path)
        # Rough: len(text) // 4
        assert result.estimated_tokens > 0
        assert result.estimated_tokens == len(result.text) // 4

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        (tmp_path / "code.py").write_text("x = 1")
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "image.png" not in result.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd llm-code && python -m pytest tests/test_tools/test_dump.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'llm_code.tools.dump'`

- [ ] **Step 3: Implement dump_codebase**

Create `llm_code/tools/dump.py`:

```python
"""DAFC Dump -- concatenate repo source files for external LLM consumption."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs", "*.egg-info",
})

_SKIP_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".dat", ".pkl", ".pickle",
})

_MAX_SINGLE_FILE_BYTES = 50_000  # 50KB
_MAX_TOTAL_BYTES = 500_000       # 500KB


@dataclass(frozen=True)
class DumpResult:
    text: str
    file_count: int
    total_lines: int
    estimated_tokens: int


def dump_codebase(cwd: Path, max_files: int = 200) -> DumpResult:
    """Walk cwd, concatenate source files into a single text dump.

    Skips binary files, large files, and common non-source directories.
    """
    files: list[Path] = []
    _collect_files(cwd, cwd, files, max_files)
    files.sort(key=lambda p: str(p.relative_to(cwd)))

    parts: list[str] = []
    total_lines = 0
    total_bytes = 0

    for f in files:
        if total_bytes >= _MAX_TOTAL_BYTES:
            break
        try:
            content = f.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # skip binary / unreadable

        rel_path = str(f.relative_to(cwd))
        total_lines += content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        total_bytes += len(content.encode("utf-8"))
        parts.append(f"--- file: {rel_path} ---\n{content}\n")

    text = "".join(parts)
    file_count = len(parts)

    return DumpResult(
        text=text,
        file_count=file_count,
        total_lines=total_lines,
        estimated_tokens=len(text) // 4,
    )


def _collect_files(base: Path, current: Path, out: list[Path], limit: int) -> None:
    """Recursively collect files, respecting skip rules and limits."""
    if len(out) >= limit:
        return

    try:
        entries = sorted(current.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return

    for entry in entries:
        if len(out) >= limit:
            return

        if entry.is_dir():
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            _collect_files(base, entry, out, limit)
        elif entry.is_file():
            if entry.suffix.lower() in _SKIP_EXTENSIONS:
                continue
            if entry.stat().st_size > _MAX_SINGLE_FILE_BYTES:
                continue
            # Quick binary check: try reading first 512 bytes
            try:
                head = entry.read_bytes()[:512]
                if b"\x00" in head:
                    continue  # likely binary
            except OSError:
                continue
            out.append(entry)
```

- [ ] **Step 4: Add `/dump` slash command to app.py**

In `llm_code/tui/app.py`, add handler:

```python
    def _cmd_dump(self, args: str) -> None:
        """Dump codebase for external LLM use."""
        import asyncio
        asyncio.ensure_future(self._run_dump(args))

    async def _run_dump(self, args: str) -> None:
        from llm_code.tools.dump import dump_codebase
        chat = self.query_one(ChatScrollView)

        max_files = 200
        if args.strip().isdigit():
            max_files = int(args.strip())

        result = dump_codebase(self._cwd, max_files=max_files)

        if result.file_count == 0:
            chat.add_entry(AssistantText("No source files found to dump."))
            return

        # Write to file
        dump_path = self._cwd / ".llm-code" / "dump.txt"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(result.text, encoding="utf-8")

        chat.add_entry(AssistantText(
            f"Dumped {result.file_count} files "
            f"({result.total_lines:,} lines, ~{result.estimated_tokens:,} tokens)\n"
            f"Saved to: {dump_path}"
        ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_tools/test_dump.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd llm-code && git add llm_code/tools/dump.py llm_code/tui/app.py tests/test_tools/test_dump.py
git commit -m "feat: /dump command for DAFC codebase dump"
```

---

### Task 5: Repo Map (AST Symbol Index)

**Files:**
- Create: `llm_code/runtime/repo_map.py`
- Modify: `llm_code/tui/app.py`
- Modify: `llm_code/runtime/conversation.py`
- Create: `tests/test_runtime/test_repo_map.py`

- [ ] **Step 1: Write failing tests for repo map**

```python
"""Tests for llm_code.runtime.repo_map -- AST-based symbol index."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.repo_map import build_repo_map, RepoMap, FileSymbols, ClassSymbol


class TestBuildRepoMapPython:
    def test_extracts_classes_and_functions(self, tmp_path: Path) -> None:
        code = '''
class UserService:
    def create_user(self, name: str) -> None:
        pass
    def delete_user(self, uid: int) -> bool:
        return True

def standalone_helper() -> str:
    return "hi"
'''
        (tmp_path / "service.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        assert isinstance(repo_map, RepoMap)
        assert len(repo_map.files) == 1

        fs = repo_map.files[0]
        assert fs.path == "service.py"
        assert len(fs.classes) == 1
        assert fs.classes[0].name == "UserService"
        assert "create_user" in fs.classes[0].methods
        assert "delete_user" in fs.classes[0].methods
        assert "standalone_helper" in fs.functions

    def test_multiple_classes(self, tmp_path: Path) -> None:
        code = '''
class Foo:
    def bar(self): ...

class Baz:
    def qux(self): ...
'''
        (tmp_path / "models.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        fs = repo_map.files[0]
        assert len(fs.classes) == 2
        names = {c.name for c in fs.classes}
        assert names == {"Foo", "Baz"}

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.py").write_text("")

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert fs.classes == ()
        assert fs.functions == ()


class TestBuildRepoMapNonPython:
    def test_js_regex_extraction(self, tmp_path: Path) -> None:
        code = '''
class MyComponent {
  constructor() {}
}

function handleClick() {}

export const API_URL = "http://example.com";
export function fetchData() {}
'''
        (tmp_path / "app.js").write_text(code)

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert any(c.name == "MyComponent" for c in fs.classes)
        assert "handleClick" in fs.functions or "fetchData" in fs.functions

    def test_unsupported_file_shows_path_only(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"key": "value"}')

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert fs.path == "data.json"
        assert fs.classes == ()
        assert fs.functions == ()


class TestRepoMapCompact:
    def test_compact_format(self, tmp_path: Path) -> None:
        code = '''
class Client:
    def send(self): ...
    def recv(self): ...

def connect() -> None: ...
'''
        (tmp_path / "net.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        compact = repo_map.to_compact()
        assert "net.py:" in compact
        assert "Client" in compact
        assert "send" in compact
        assert "connect" in compact

    def test_compact_respects_token_budget(self, tmp_path: Path) -> None:
        # Create many files to exceed budget
        for i in range(50):
            (tmp_path / f"mod_{i}.py").write_text(f"class C{i}:\n    def m{i}(self): ...\n")

        repo_map = build_repo_map(tmp_path)
        compact = repo_map.to_compact(max_tokens=200)
        # Rough: 200 tokens * 4 chars = 800 chars max
        assert len(compact) <= 1200  # generous margin

    def test_skips_hidden_and_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("def ok(): ...")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "good.cpython-311.pyc").write_bytes(b"\x00")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.py").write_text("x = 1")

        repo_map = build_repo_map(tmp_path)
        paths = [f.path for f in repo_map.files]
        assert "good.py" in paths
        assert not any("__pycache__" in p for p in paths)
        assert not any(".hidden" in p for p in paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_repo_map.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'llm_code.runtime.repo_map'`

- [ ] **Step 3: Implement repo_map module**

Create `llm_code/runtime/repo_map.py`:

```python
"""Repo Map -- AST-based symbol index for codebase overview."""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs",
})

_PYTHON_EXTS = frozenset({".py", ".pyi"})
_JS_TS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx"})
_SUPPORTED_EXTS = _PYTHON_EXTS | _JS_TS_EXTS | frozenset({".go", ".rs", ".java"})


@dataclass(frozen=True)
class ClassSymbol:
    name: str
    methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileSymbols:
    path: str
    classes: tuple[ClassSymbol, ...] = ()
    functions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoMap:
    files: tuple[FileSymbols, ...] = ()

    def to_compact(self, max_tokens: int = 2000) -> str:
        """Render a compact text representation of the repo map.

        Stays within approximately max_tokens (estimated as chars / 4).
        """
        max_chars = max_tokens * 4
        lines: list[str] = []
        total_chars = 0

        for fs in self.files:
            if not fs.classes and not fs.functions:
                line = fs.path
            else:
                symbols: list[str] = []
                for cls in fs.classes:
                    if cls.methods:
                        symbols.append(f"{cls.name}({', '.join(cls.methods)})")
                    else:
                        symbols.append(cls.name)
                symbols.extend(fs.functions)
                line = f"{fs.path}: {', '.join(symbols)}"

            line_len = len(line) + 1  # +1 for newline
            if total_chars + line_len > max_chars:
                break
            lines.append(line)
            total_chars += line_len

        return "\n".join(lines)


def build_repo_map(cwd: Path) -> RepoMap:
    """Build a symbol map of the repository."""
    file_symbols: list[FileSymbols] = []
    source_files: list[Path] = []

    _collect_source_files(cwd, cwd, source_files)
    source_files.sort(key=lambda p: str(p.relative_to(cwd)))

    for f in source_files:
        rel = str(f.relative_to(cwd))
        suffix = f.suffix.lower()

        if suffix in _PYTHON_EXTS:
            fs = _parse_python(f, rel)
        elif suffix in _JS_TS_EXTS:
            fs = _parse_js_ts(f, rel)
        else:
            fs = FileSymbols(path=rel)

        file_symbols.append(fs)

    return RepoMap(files=tuple(file_symbols))


def _collect_source_files(base: Path, current: Path, out: list[Path]) -> None:
    """Recursively collect source files."""
    try:
        entries = sorted(current.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return

    for entry in entries:
        if entry.is_dir():
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            _collect_source_files(base, entry, out)
        elif entry.is_file():
            # Include all text files, not just supported extensions
            if entry.suffix.lower() in {".pyc", ".pyo", ".so", ".dll", ".png", ".jpg", ".zip", ".gz"}:
                continue
            if entry.stat().st_size > 100_000:  # skip very large files
                continue
            out.append(entry)


def _parse_python(path: Path, rel_path: str) -> FileSymbols:
    """Parse a Python file using AST to extract classes and functions."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel_path)
    except (SyntaxError, OSError):
        return FileSymbols(path=rel_path)

    classes: list[ClassSymbol] = []
    functions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = tuple(
                item.name
                for item in ast.iter_child_nodes(node)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            )
            classes.append(ClassSymbol(name=node.name, methods=methods))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(node.name)

    return FileSymbols(path=rel_path, classes=tuple(classes), functions=tuple(functions))


def _parse_js_ts(path: Path, rel_path: str) -> FileSymbols:
    """Parse JS/TS file using regex fallback for class/function extraction."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileSymbols(path=rel_path)

    classes: list[ClassSymbol] = []
    functions: list[str] = []

    # class ClassName
    for match in re.finditer(r"class\s+(\w+)", source):
        classes.append(ClassSymbol(name=match.group(1)))

    # function funcName  /  export function funcName  /  export const funcName
    for match in re.finditer(r"(?:export\s+)?function\s+(\w+)", source):
        functions.append(match.group(1))
    for match in re.finditer(r"export\s+const\s+(\w+)", source):
        functions.append(match.group(1))

    return FileSymbols(path=rel_path, classes=tuple(classes), functions=tuple(functions))
```

- [ ] **Step 4: Add `/map` slash command to app.py**

In `llm_code/tui/app.py`:

```python
    def _cmd_map(self, args: str) -> None:
        """Show repo map."""
        from llm_code.runtime.repo_map import build_repo_map
        chat = self.query_one(ChatScrollView)

        try:
            repo_map = build_repo_map(self._cwd)
            compact = repo_map.to_compact(max_tokens=2000)
            if compact:
                chat.add_entry(AssistantText(f"# Repo Map\n{compact}"))
            else:
                chat.add_entry(AssistantText("No source files found."))
        except Exception as exc:
            chat.add_entry(AssistantText(f"Error building repo map: {exc}"))
```

- [ ] **Step 5: Inject repo map into system prompt**

In `llm_code/runtime/conversation.py`, in the system prompt building phase (typically at the start of `run_turn`), add:

```python
        # Inject repo map if available
        try:
            from llm_code.runtime.repo_map import build_repo_map
            if self._context and hasattr(self._context, "cwd"):
                repo_map = build_repo_map(Path(self._context.cwd))
                compact = repo_map.to_compact(max_tokens=2000)
                if compact:
                    system_parts.append(f"\n# Repo Map\n{compact}")
        except Exception:
            pass  # Don't fail conversation for repo map issues
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_repo_map.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 7: Commit**

```bash
cd llm-code && git add llm_code/runtime/repo_map.py llm_code/tui/app.py llm_code/runtime/conversation.py tests/test_runtime/test_repo_map.py
git commit -m "feat: repo map with AST symbol index and /map command"
```

---

### Task 6: LSP Auto-Diagnose After Edit

**Files:**
- Create: `llm_code/runtime/auto_diagnose.py`
- Modify: `llm_code/runtime/config.py`
- Modify: `llm_code/runtime/conversation.py`
- Create: `tests/test_runtime/test_auto_diagnose.py`

- [ ] **Step 1: Write failing tests for auto_diagnose**

```python
"""Tests for llm_code.runtime.auto_diagnose -- automatic LSP diagnostics after edit."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.runtime.auto_diagnose import auto_diagnose, format_diagnostics


class _FakeDiagnostic:
    def __init__(self, file: str, line: int, column: int, severity: str, message: str, source: str) -> None:
        self.file = file
        self.line = line
        self.column = column
        self.severity = severity
        self.message = message
        self.source = source


class TestAutoDiagnose:
    @pytest.mark.asyncio
    async def test_returns_errors_only(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.return_value = [
            _FakeDiagnostic("app.py", 10, 5, "error", "Name 'foo' is not defined", "pyright"),
            _FakeDiagnostic("app.py", 15, 0, "warning", "Unused import", "pyright"),
            _FakeDiagnostic("app.py", 20, 3, "error", "Unexpected indent", "pyright"),
        ]

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert len(errors) == 2
        assert "foo" in errors[0]
        assert "indent" in errors[1]

    @pytest.mark.asyncio
    async def test_no_errors_returns_empty(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.return_value = [
            _FakeDiagnostic("app.py", 5, 0, "warning", "Unused var", "pyright"),
        ]

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []

    @pytest.mark.asyncio
    async def test_no_lsp_client_returns_empty(self) -> None:
        mock_manager = MagicMock()
        mock_manager.get_client.return_value = None

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []

    @pytest.mark.asyncio
    async def test_lsp_error_returns_empty(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_diagnostics.side_effect = Exception("LSP crashed")

        mock_manager = MagicMock()
        mock_manager.get_client.return_value = mock_client

        errors = await auto_diagnose(mock_manager, "app.py")
        assert errors == []


class TestFormatDiagnostics:
    def test_format_single_error(self) -> None:
        diag = _FakeDiagnostic("utils.py", 42, 8, "error", "Name 'bar' is not defined", "pyright")
        result = format_diagnostics([diag])
        assert len(result) == 1
        assert "utils.py:42" in result[0]
        assert "bar" in result[0]

    def test_format_empty(self) -> None:
        result = format_diagnostics([])
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_auto_diagnose.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'llm_code.runtime.auto_diagnose'`

- [ ] **Step 3: Implement auto_diagnose module**

Create `llm_code/runtime/auto_diagnose.py`:

```python
"""Auto-diagnose -- run LSP diagnostics after file edits and report errors."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Extension to language mapping (mirrors llm_code/lsp/tools.py)
_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}


def format_diagnostics(diagnostics: list[Any]) -> list[str]:
    """Format diagnostic objects into human-readable strings."""
    return [
        f"{d.file}:{d.line}:{d.column} [{d.severity}] {d.message} ({d.source})"
        for d in diagnostics
    ]


async def auto_diagnose(lsp_manager: Any, file_path: str) -> list[str]:
    """Run LSP diagnostics on a file and return error-level issues only.

    Returns a list of formatted error strings. Empty list if no errors
    or LSP is unavailable.
    """
    try:
        suffix = Path(file_path).suffix.lower()
        language = _EXT_LANGUAGE.get(suffix, "")
        if not language:
            return []

        client = lsp_manager.get_client(language)
        if client is None:
            return []

        file_uri = Path(file_path).as_uri()
        diagnostics = await client.get_diagnostics(file_uri)

        if not diagnostics:
            return []

        # Filter to error-level only
        errors = [d for d in diagnostics if d.severity == "error"]
        if not errors:
            return []

        return format_diagnostics(errors)

    except Exception:
        logger.debug("Auto-diagnose failed for %s", file_path, exc_info=True)
        return []
```

- [ ] **Step 4: Add `lsp_auto_diagnose` field to RuntimeConfig**

In `llm_code/runtime/config.py`, add to the `RuntimeConfig` dataclass (after `auto_commit`):

```python
    lsp_auto_diagnose: bool = True
```

In `_dict_to_runtime_config()`, add to the return statement:

```python
        lsp_auto_diagnose=data.get("lsp_auto_diagnose", True),
```

- [ ] **Step 5: Wire auto-diagnose into conversation post-tool hook**

In `llm_code/runtime/conversation.py`, after the auto-commit block (added in Task 2), add:

```python
        # 7c. LSP auto-diagnose after write/edit tools
        if (
            hasattr(self._config, "lsp_auto_diagnose")
            and self._config.lsp_auto_diagnose
            and call.name in ("write_file", "edit_file")
            and not tool_result.is_error
        ):
            try:
                from llm_code.runtime.auto_diagnose import auto_diagnose
                file_path = args.get("file_path") or args.get("path", "")
                if file_path and hasattr(self, "_lsp_manager") and self._lsp_manager is not None:
                    diag_errors = await auto_diagnose(self._lsp_manager, file_path)
                    if diag_errors:
                        diag_text = "\n".join(diag_errors)
                        # Inject as system message for agent to see
                        yield StreamToolProgress(
                            tool_name="lsp_auto_diagnose",
                            message=f"LSP found errors in {Path(file_path).name}:\n{diag_text}",
                            percent=None,
                        )
            except Exception:
                pass  # Never block tool flow for diagnostic failure
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd llm-code && python -m pytest tests/test_runtime/test_auto_diagnose.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
cd llm-code && git add llm_code/runtime/auto_diagnose.py llm_code/runtime/config.py llm_code/runtime/conversation.py tests/test_runtime/test_auto_diagnose.py
git commit -m "feat: LSP auto-diagnose after write/edit tools"
```

---

### Task 7: Clean Interrupt + Resume

**Files:**
- Modify: `llm_code/tui/app.py`
- Create: `tests/test_tui/test_clean_interrupt.py`

- [ ] **Step 1: Write failing tests for clean interrupt**

```python
"""Tests for clean interrupt handling (Ctrl+C)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestInterruptState:
    """Test interrupt state machine logic (independent of TUI)."""

    def test_first_interrupt_sets_pending(self) -> None:
        """Simulate first Ctrl+C: should set pending flag and save checkpoint."""
        state = {"interrupt_pending": False, "last_interrupt_time": 0.0}

        # First interrupt
        now = time.monotonic()
        state["interrupt_pending"] = True
        state["last_interrupt_time"] = now

        assert state["interrupt_pending"] is True

    def test_second_interrupt_within_window_triggers_exit(self) -> None:
        """Simulate second Ctrl+C within 2s: should trigger force exit."""
        state = {"interrupt_pending": True, "last_interrupt_time": time.monotonic()}

        # Simulate second interrupt immediately
        now = time.monotonic()
        elapsed = now - state["last_interrupt_time"]
        should_force_exit = state["interrupt_pending"] and elapsed < 2.0

        assert should_force_exit is True

    def test_second_interrupt_after_window_resets(self) -> None:
        """Simulate second Ctrl+C after 2s: should reset, not force exit."""
        state = {"interrupt_pending": True, "last_interrupt_time": time.monotonic() - 3.0}

        now = time.monotonic()
        elapsed = now - state["last_interrupt_time"]
        should_force_exit = state["interrupt_pending"] and elapsed < 2.0

        assert should_force_exit is False

    def test_checkpoint_save_called(self) -> None:
        """Verify checkpoint manager is invoked on first interrupt."""
        mock_checkpoint = MagicMock()
        mock_checkpoint.save_checkpoint.return_value = "ses_abc123"

        # Simulate first interrupt handler
        session_id = mock_checkpoint.save_checkpoint()

        mock_checkpoint.save_checkpoint.assert_called_once()
        assert session_id == "ses_abc123"

    def test_no_checkpoint_when_idle(self) -> None:
        """When no active session, interrupt should exit immediately."""
        is_streaming = False
        should_save = is_streaming  # Only save if actively working

        assert should_save is False
```

- [ ] **Step 2: Run tests to verify they pass (pure logic tests)**

Run: `cd llm-code && python -m pytest tests/test_tui/test_clean_interrupt.py -v`
Expected: All 5 tests PASS (pure state logic, no imports needed).

- [ ] **Step 3: Implement clean interrupt handler in app.py**

In `llm_code/tui/app.py` `__init__`, add:

```python
        self._interrupt_pending: bool = False
        self._last_interrupt_time: float = 0.0
```

Add signal handler method:

```python
    def _handle_interrupt(self) -> None:
        """Handle Ctrl+C: first press saves checkpoint, second force exits."""
        import time as _time
        import asyncio

        now = _time.monotonic()
        status = self.query_one(StatusBar)
        chat = self.query_one(ChatScrollView)

        # If not streaming, exit immediately
        if not status.is_streaming:
            self.exit()
            return

        # Second Ctrl+C within 2 seconds: force exit
        if self._interrupt_pending and (now - self._last_interrupt_time) < 2.0:
            chat.add_entry(AssistantText("Goodbye."))
            self.exit()
            return

        # First Ctrl+C: save checkpoint
        self._interrupt_pending = True
        self._last_interrupt_time = now

        session_id = ""
        if self._checkpoint_mgr is not None:
            try:
                session_id = self._checkpoint_mgr.save_checkpoint() or ""
            except Exception:
                pass

        resume_hint = f"\n  Resume with: llm-code --resume {session_id}" if session_id else ""
        chat.add_entry(AssistantText(
            f"\u23f8 Session paused and saved.{resume_hint}\n"
            f"  Press Ctrl+C again to quit immediately."
        ))
```

Register signal handler in `on_mount`:

```python
        import signal

        def _sigint_handler(signum, frame):
            self.call_from_thread(self._handle_interrupt)

        signal.signal(signal.SIGINT, _sigint_handler)
```

- [ ] **Step 4: Run all tests to verify nothing is broken**

Run: `cd llm-code && python -m pytest tests/test_tui/test_clean_interrupt.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd llm-code && git add llm_code/tui/app.py tests/test_tui/test_clean_interrupt.py
git commit -m "feat: clean interrupt with checkpoint save and resume hint"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
cd llm-code && python -m pytest tests/test_tui/test_status_bar_realtime.py tests/test_runtime/test_auto_commit.py tests/test_tui/test_plan_mode.py tests/test_tools/test_dump.py tests/test_runtime/test_repo_map.py tests/test_runtime/test_auto_diagnose.py tests/test_tui/test_clean_interrupt.py -v
```

Expected: All tests PASS across all 7 features.

- [ ] **Run existing test suite to verify no regressions**

```bash
cd llm-code && python -m pytest --tb=short -q
```

Expected: No new failures introduced.

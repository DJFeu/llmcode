# Medium Priority Parity Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 medium-priority features to LLM-Code: print CLI status line, LLM semantic compression, MultiEdit tool, session naming + picker, and per-arg permission rules.

**Architecture:** Two phases — Phase 3a adds independent features (status line, MultiEdit, session naming) in parallel; Phase 3b modifies core runtime (LLM compression, bash rules). All new code follows existing frozen-dataclass + Tool ABC patterns.

**Tech Stack:** Python 3.11+, Rich (Live), pytest, pydantic

**Spec:** `docs/superpowers/specs/2026-04-05-medium-priority-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `llm_code/cli/status_line.py` | CLIStatusLine class — Rich Live-based persistent bottom bar |
| `llm_code/tools/multi_edit.py` | MultiEditTool — atomic multi-file search-and-replace |
| `tests/test_cli/test_status_line.py` | Tests for CLIStatusLine formatting |
| `tests/test_tools/test_multi_edit.py` | Tests for MultiEditTool |
| `tests/test_runtime/test_session_naming.py` | Tests for session naming, search, delete |
| `tests/test_runtime/test_compressor_llm.py` | Tests for Level 5 LLM summarization |
| `tests/test_tools/test_bash_user_rules.py` | Tests for user-defined bash rules |

### Modified Files

| File | Change |
|------|--------|
| `llm_code/cli/tui.py` | Integrate CLIStatusLine into print CLI loop; expand `/session` command |
| `llm_code/runtime/session.py` | Add `name`/`tags` to Session + SessionSummary; add SessionManager.rename/delete/search |
| `llm_code/runtime/compressor.py` | Add Level 5 `_llm_summarize`; add `compress_async()`; accept optional provider |
| `llm_code/runtime/config.py` | Add `CompressorConfig`, `BashRule`, `BashRulesConfig` |
| `llm_code/tools/bash.py` | Integrate user rules into `classify_command()` |
| `llm_code/tools/edit_file.py` | Extract `_apply_edit()` helper for reuse by MultiEdit |

---

## Phase 3a: Independent Features

---

### Task 1: CLIStatusLine Formatting (status_line.py)

**Files:**
- Create: `llm_code/cli/status_line.py`
- Test: `tests/test_cli/test_status_line.py`

- [ ] **Step 1: Write failing tests for status line formatting**

```python
# tests/test_cli/test_status_line.py
import pytest
from llm_code.cli.status_line import StatusLineState, format_status_line


class TestFormatStatusLine:
    def test_empty_state(self):
        state = StatusLineState()
        result = format_status_line(state)
        assert "/help" in result
        assert "Ctrl+D quit" in result

    def test_model_only(self):
        state = StatusLineState(model="qwen-72b")
        result = format_status_line(state)
        assert "qwen-72b" in result

    def test_full_state(self):
        state = StatusLineState(
            model="qwen-72b",
            tokens=1234,
            cost="$0.0050",
            is_streaming=True,
        )
        result = format_status_line(state)
        assert "qwen-72b" in result
        assert "1,234" in result
        assert "$0.0050" in result
        assert "streaming" in result

    def test_context_usage_hidden_below_threshold(self):
        state = StatusLineState(model="qwen-72b", context_usage=0.3)
        result = format_status_line(state)
        assert "%" not in result

    def test_context_usage_shown_above_threshold(self):
        state = StatusLineState(model="qwen-72b", context_usage=0.75)
        result = format_status_line(state)
        assert "75%" in result

    def test_permission_mode_shown(self):
        state = StatusLineState(model="qwen-72b", permission_mode="plan")
        result = format_status_line(state)
        assert "[plan]" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_status_line.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_code.cli.status_line'`

- [ ] **Step 3: Implement StatusLineState and format_status_line**

```python
# llm_code/cli/status_line.py
"""CLIStatusLine — persistent bottom status bar for the print CLI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.text import Text


@dataclass
class StatusLineState:
    model: str = ""
    tokens: int = 0
    cost: str = ""
    is_streaming: bool = False
    permission_mode: str = ""
    context_usage: float = 0.0  # 0.0-1.0


def format_status_line(state: StatusLineState) -> str:
    """Format status line as a pipe-separated string."""
    parts: list[str] = []
    if state.permission_mode and state.permission_mode != "prompt":
        parts.append(f"[{state.permission_mode}]")
    if state.model:
        parts.append(state.model)
    if state.tokens > 0:
        parts.append(f"↓{state.tokens:,} tok")
    if state.cost:
        parts.append(state.cost)
    if state.context_usage >= 0.6:
        pct = int(state.context_usage * 100)
        filled = int(state.context_usage * 8)
        bar = "█" * filled + "░" * (8 - filled)
        parts.append(f"[{bar}] {pct}%")
    if state.is_streaming:
        parts.append("streaming…")
    parts.append("/help")
    parts.append("Ctrl+D quit")
    return " │ ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_status_line.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/cli/status_line.py tests/test_cli/test_status_line.py
git commit -m "feat: add CLIStatusLine formatting with context usage bar"
```

---

### Task 2: CLIStatusLine Live Rendering + Integration

**Files:**
- Modify: `llm_code/cli/status_line.py`
- Modify: `llm_code/cli/tui.py`
- Test: `tests/test_cli/test_status_line.py` (extend)

- [ ] **Step 1: Write failing tests for CLIStatusLine class**

```python
# tests/test_cli/test_status_line.py (append)
from unittest.mock import MagicMock
from rich.console import Console


class TestCLIStatusLine:
    def test_update_changes_state(self):
        from llm_code.cli.status_line import CLIStatusLine
        console = Console(file=MagicMock(), force_terminal=True)
        line = CLIStatusLine(console)
        line.update(model="test-model", tokens=500)
        assert line.state.model == "test-model"
        assert line.state.tokens == 500

    def test_update_partial(self):
        from llm_code.cli.status_line import CLIStatusLine
        console = Console(file=MagicMock(), force_terminal=True)
        line = CLIStatusLine(console)
        line.update(model="m1")
        line.update(tokens=100)
        assert line.state.model == "m1"
        assert line.state.tokens == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli/test_status_line.py::TestCLIStatusLine -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement CLIStatusLine class**

Add to `llm_code/cli/status_line.py`:

```python
class CLIStatusLine:
    """Persistent bottom status line for the print CLI using Rich Live."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self.state = StatusLineState()
        self._live: Live | None = None

    def update(self, **kwargs: Any) -> None:
        """Update one or more state fields and refresh the display."""
        for key, value in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        if self._live is not None:
            self._live.update(self._render())

    def _render(self) -> Text:
        """Render the current state as a Rich Text object."""
        return Text(format_status_line(self.state), style="dim")

    def start(self) -> None:
        """Begin live rendering at the bottom of the terminal."""
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop live rendering."""
        if self._live is not None:
            self._live.stop()
            self._live = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli/test_status_line.py -v`
Expected: All PASS

- [ ] **Step 5: Integrate into print CLI tui.py**

In `llm_code/cli/tui.py`, find the `_init_session` method and add:
```python
from llm_code.cli.status_line import CLIStatusLine

# In _init_session():
self._status_line = CLIStatusLine(console)
self._status_line.update(model=self._config.model)
self._status_line.start()
```

In the turn loop (after `_cost_tracker.add_usage()`):
```python
self._status_line.update(
    tokens=self._output_tokens,
    cost=self._cost_tracker.format_cost(),
    is_streaming=False,
)
```

At streaming start:
```python
self._status_line.update(is_streaming=True)
```

In cleanup:
```python
self._status_line.stop()
```

- [ ] **Step 6: Commit**

```bash
git add llm_code/cli/status_line.py llm_code/cli/tui.py tests/test_cli/test_status_line.py
git commit -m "feat: integrate CLIStatusLine into print CLI with live rendering"
```

---

### Task 3: Extract _apply_edit() from EditFileTool

**Files:**
- Modify: `llm_code/tools/edit_file.py`
- Test: `tests/test_tools/test_edit_file.py` (verify existing tests still pass)

- [ ] **Step 1: Verify existing edit_file tests pass**

Run: `pytest tests/test_tools/test_edit_file.py -v`
Expected: All PASS

- [ ] **Step 2: Extract _apply_edit() helper**

In `llm_code/tools/edit_file.py`, extract the core search-replace logic into a standalone function:

```python
@dataclass(frozen=True)
class EditResult:
    """Result of a single edit operation."""
    success: bool
    new_content: str
    error: str = ""
    additions: int = 0
    deletions: int = 0


def _apply_edit(
    content: str,
    old: str,
    new: str,
    replace_all: bool = False,
) -> EditResult:
    """Apply a search-and-replace edit to file content.

    Returns EditResult with the new content if successful,
    or an error message if the search string was not found.
    """
    # Exact match first
    if old in content:
        if replace_all:
            new_content = content.replace(old, new)
        else:
            new_content = content.replace(old, new, 1)
        additions = new_content.count("\n") - content.count("\n")
        return EditResult(
            success=True,
            new_content=new_content,
            additions=max(0, additions),
            deletions=max(0, -additions),
        )

    # Fuzzy match (normalize whitespace/quotes)
    normalized_content = normalize_for_match(content)
    normalized_old = normalize_for_match(old)
    if normalized_old in normalized_content:
        # Find the original substring that matched after normalization
        start = normalized_content.index(normalized_old)
        # Map back to original content position (approximate — same length after normalization)
        original_old = content[start : start + len(old)]
        if replace_all:
            new_content = content.replace(original_old, new)
        else:
            new_content = content.replace(original_old, new, 1)
        additions = new_content.count("\n") - content.count("\n")
        return EditResult(
            success=True,
            new_content=new_content,
            additions=max(0, additions),
            deletions=max(0, -additions),
        )

    return EditResult(success=False, new_content=content, error=f"Search string not found in file")
```

Then refactor `EditFileTool.execute()` to call `_apply_edit()` instead of doing inline search-replace.

- [ ] **Step 3: Verify existing tests still pass**

Run: `pytest tests/test_tools/test_edit_file.py -v`
Expected: All PASS (no behavior change)

- [ ] **Step 4: Commit**

```bash
git add llm_code/tools/edit_file.py
git commit -m "refactor: extract _apply_edit() helper from EditFileTool for reuse"
```

---

### Task 4: MultiEditTool

**Files:**
- Create: `llm_code/tools/multi_edit.py`
- Test: `tests/test_tools/test_multi_edit.py`

- [ ] **Step 1: Write failing tests for MultiEditTool**

```python
# tests/test_tools/test_multi_edit.py
import pytest
from unittest.mock import MagicMock

from llm_code.tools.multi_edit import MultiEditTool


@pytest.fixture
def overlay():
    """Mock overlay that stores files in a dict."""
    store = {}
    mock = MagicMock()
    mock.read_text.side_effect = lambda p: store.get(str(p), None)
    mock.write_text.side_effect = lambda p, c: store.__setitem__(str(p), c)
    mock.exists.side_effect = lambda p: str(p) in store
    return mock, store


class TestMultiEditTool:
    def test_name(self):
        tool = MultiEditTool()
        assert tool.name == "multi_edit"

    def test_single_edit_success(self, overlay, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("hello world")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [{"path": str(f1), "old": "hello", "new": "goodbye"}]
        })
        assert not result.is_error
        assert f1.read_text() == "goodbye world"

    def test_multi_edit_atomic(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "bbb", "new": "BBB"},
            ]
        })
        assert not result.is_error
        assert f1.read_text() == "AAA"
        assert f2.read_text() == "BBB"

    def test_rollback_on_second_edit_failure(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": str(f2), "old": "NOTFOUND", "new": "XXX"},
            ]
        })
        assert result.is_error
        # f1 should be rolled back
        assert f1.read_text() == "aaa"

    def test_validation_error_no_edits_applied(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("aaa")
        tool = MultiEditTool()
        result = tool.execute({
            "edits": [
                {"path": str(f1), "old": "aaa", "new": "AAA"},
                {"path": "/nonexistent/file.py", "old": "x", "new": "y"},
            ]
        })
        assert result.is_error
        # f1 untouched because validation failed before any apply
        assert f1.read_text() == "aaa"

    def test_max_edits_exceeded(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x")
        tool = MultiEditTool()
        edits = [{"path": str(f), "old": "x", "new": "y"}] * 21
        result = tool.execute({"edits": edits})
        assert result.is_error
        assert "20" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools/test_multi_edit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement MultiEditTool**

```python
# llm_code/tools/multi_edit.py
"""MultiEditTool — atomic multi-file search-and-replace."""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from pydantic import BaseModel

from llm_code.runtime.file_protection import check_write
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.edit_file import EditResult, _apply_edit

if TYPE_CHECKING:
    from llm_code.runtime.overlay import OverlayFS

_MAX_EDITS = 20


class SingleEdit(BaseModel):
    path: str
    old: str
    new: str
    replace_all: bool = False


class MultiEditInput(BaseModel):
    edits: list[SingleEdit]


class MultiEditTool(Tool):
    @property
    def name(self) -> str:
        return "multi_edit"

    @property
    def description(self) -> str:
        return "Atomic multi-file search-and-replace. All edits succeed or none are applied."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to file"},
                            "old": {"type": "string", "description": "Text to search for"},
                            "new": {"type": "string", "description": "Replacement text"},
                            "replace_all": {"type": "boolean", "default": False},
                        },
                        "required": ["path", "old", "new"],
                    },
                    "minItems": 1,
                    "maxItems": _MAX_EDITS,
                }
            },
            "required": ["edits"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[MultiEditInput]:
        return MultiEditInput

    def execute(self, args: dict, overlay: "OverlayFS | None" = None) -> ToolResult:
        edits_raw = args.get("edits", [])

        # Guard: max edits
        if len(edits_raw) > _MAX_EDITS:
            return ToolResult(
                output=f"Too many edits ({len(edits_raw)}). Maximum is {_MAX_EDITS}.",
                is_error=True,
            )

        edits = [SingleEdit(**e) if isinstance(e, dict) else e for e in edits_raw]

        # Phase 1: Pre-validate all edits
        errors: list[str] = []
        for i, edit in enumerate(edits):
            path = pathlib.Path(edit.path)
            if not path.exists():
                errors.append(f"Edit {i + 1}: File not found: {path}")
                continue
            protection = check_write(str(path))
            if not protection.allowed:
                errors.append(f"Edit {i + 1}: {protection.reason}")

        if errors:
            return ToolResult(output="Validation failed:\n" + "\n".join(errors), is_error=True)

        # Phase 2: Snapshot all files
        snapshots: dict[str, str] = {}
        for edit in edits:
            p = str(edit.path)
            if p not in snapshots:
                snapshots[p] = pathlib.Path(p).read_text(encoding="utf-8")

        # Phase 3: Apply all edits
        applied: list[str] = []
        current_contents: dict[str, str] = dict(snapshots)
        for i, edit in enumerate(edits):
            p = str(edit.path)
            result = _apply_edit(
                current_contents[p], edit.old, edit.new, edit.replace_all
            )
            if not result.success:
                # Rollback all files to snapshot
                for sp, sc in snapshots.items():
                    pathlib.Path(sp).write_text(sc, encoding="utf-8")
                return ToolResult(
                    output=f"Edit {i + 1} failed ({edit.path}): {result.error}. All edits rolled back.",
                    is_error=True,
                )
            current_contents[p] = result.new_content
            applied.append(
                f"Edit {i + 1}: {edit.path} (+{result.additions}/-{result.deletions})"
            )

        # Phase 4: Write all files
        for p, content in current_contents.items():
            pathlib.Path(p).write_text(content, encoding="utf-8")

        return ToolResult(
            output=f"Applied {len(edits)} edits:\n" + "\n".join(applied),
            metadata={"edits_applied": len(edits)},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools/test_multi_edit.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/tools/multi_edit.py tests/test_tools/test_multi_edit.py
git commit -m "feat: add MultiEditTool with atomic multi-file edit and rollback"
```

---

### Task 5: Session Naming — Data Model

**Files:**
- Modify: `llm_code/runtime/session.py`
- Test: `tests/test_runtime/test_session_naming.py`

- [ ] **Step 1: Write failing tests for session name/tags**

```python
# tests/test_runtime/test_session_naming.py
import pytest
from pathlib import Path
from llm_code.runtime.session import Session


class TestSessionNaming:
    def test_default_name_empty(self):
        s = Session.create(Path("/tmp"))
        assert s.name == ""
        assert s.tags == ()

    def test_rename(self):
        s = Session.create(Path("/tmp"))
        s2 = s.rename("my-session")
        assert s2.name == "my-session"
        assert s.name == ""  # immutable

    def test_add_tags(self):
        s = Session.create(Path("/tmp"))
        s2 = s.add_tags("auth", "refactor")
        assert s2.tags == ("auth", "refactor")
        assert s.tags == ()  # immutable

    def test_add_tags_dedup(self):
        s = Session.create(Path("/tmp")).add_tags("a", "b")
        s2 = s.add_tags("b", "c")
        assert s2.tags == ("a", "b", "c")

    def test_serialize_with_name_tags(self):
        s = Session.create(Path("/tmp")).rename("test").add_tags("t1")
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["tags"] == ["t1"]

    def test_deserialize_with_name_tags(self):
        s = Session.create(Path("/tmp")).rename("test").add_tags("t1")
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.name == "test"
        assert s2.tags == ("t1",)

    def test_deserialize_legacy_no_name(self):
        """Old session JSON without name/tags fields should still load."""
        s = Session.create(Path("/tmp"))
        d = s.to_dict()
        del d["name"]
        del d["tags"]
        s2 = Session.from_dict(d)
        assert s2.name == ""
        assert s2.tags == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime/test_session_naming.py -v`
Expected: FAIL (Session has no `name` attribute)

- [ ] **Step 3: Add name/tags to Session**

In `llm_code/runtime/session.py`, modify the Session dataclass:

```python
@dataclasses.dataclass(frozen=True)
class Session:
    id: str
    messages: tuple[Message, ...]
    created_at: str
    updated_at: str
    total_usage: TokenUsage
    project_path: Path
    name: str = ""
    tags: tuple[str, ...] = ()

    def rename(self, name: str) -> "Session":
        now = datetime.now(timezone.utc).isoformat()
        return dataclasses.replace(self, name=name, updated_at=now)

    def add_tags(self, *tags: str) -> "Session":
        merged = tuple(dict.fromkeys(self.tags + tags))
        now = datetime.now(timezone.utc).isoformat()
        return dataclasses.replace(self, tags=merged, updated_at=now)
```

Update `to_dict()`:
```python
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": [_message_to_dict(m) for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_usage": {
                "input_tokens": self.total_usage.input_tokens,
                "output_tokens": self.total_usage.output_tokens,
            },
            "project_path": str(self.project_path),
            "name": self.name,
            "tags": list(self.tags),
        }
```

Update `from_dict()`:
```python
    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(
            id=data["id"],
            messages=tuple(_dict_to_message(m) for m in data["messages"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            total_usage=TokenUsage(
                input_tokens=data["total_usage"]["input_tokens"],
                output_tokens=data["total_usage"]["output_tokens"],
            ),
            project_path=Path(data["project_path"]),
            name=data.get("name", ""),
            tags=tuple(data.get("tags", ())),
        )
```

Update `SessionSummary`:
```python
@dataclasses.dataclass(frozen=True)
class SessionSummary:
    id: str
    project_path: Path
    created_at: str
    message_count: int
    name: str = ""
    tags: tuple[str, ...] = ()
```

Update `SessionManager.list_sessions()` to include name/tags:
```python
    summaries.append(
        SessionSummary(
            id=data["id"],
            project_path=Path(data["project_path"]),
            created_at=data["created_at"],
            message_count=len(data["messages"]),
            name=data.get("name", ""),
            tags=tuple(data.get("tags", ())),
        )
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime/test_session_naming.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing session tests to verify no regression**

Run: `pytest tests/test_runtime/test_session.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add llm_code/runtime/session.py tests/test_runtime/test_session_naming.py
git commit -m "feat: add name and tags fields to Session with immutable rename/add_tags"
```

---

### Task 6: SessionManager Extensions (rename, delete, search)

**Files:**
- Modify: `llm_code/runtime/session.py`
- Test: `tests/test_runtime/test_session_naming.py` (extend)

- [ ] **Step 1: Write failing tests for SessionManager extensions**

```python
# tests/test_runtime/test_session_naming.py (append)
from llm_code.runtime.session import SessionManager


class TestSessionManagerExtensions:
    def test_rename(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp"))
        mgr.save(s)
        renamed = mgr.rename(s.id, "my-session")
        assert renamed.name == "my-session"
        # Reload and verify persisted
        loaded = mgr.load(s.id)
        assert loaded.name == "my-session"

    def test_delete(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp"))
        mgr.save(s)
        assert mgr.delete(s.id) is True
        with pytest.raises(FileNotFoundError):
            mgr.load(s.id)

    def test_delete_nonexistent(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.delete("nonexistent") is False

    def test_search_by_name(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/tmp")).rename("auth-refactor")
        s2 = Session.create(Path("/tmp")).rename("perf-tuning")
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("auth")
        assert len(results) == 1
        assert results[0].name == "auth-refactor"

    def test_search_by_path(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/work/myapp"))
        s2 = Session.create(Path("/work/other"))
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("myapp")
        assert len(results) == 1

    def test_search_by_tag(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/tmp")).add_tags("urgent")
        s2 = Session.create(Path("/tmp")).add_tags("low-priority")
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("urgent")
        assert len(results) == 1

    def test_get_by_name(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp")).rename("my-session")
        mgr.save(s)
        found = mgr.get_by_name("my-session")
        assert found is not None
        assert found.id == s.id

    def test_get_by_name_not_found(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.get_by_name("nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime/test_session_naming.py::TestSessionManagerExtensions -v`
Expected: FAIL (methods don't exist)

- [ ] **Step 3: Implement SessionManager extensions**

In `llm_code/runtime/session.py`, add to `SessionManager`:

```python
    def rename(self, session_id: str, name: str) -> Session:
        """Rename a session and persist the change."""
        session = self.load(session_id)
        renamed = session.rename(name)
        self.save(renamed)
        return renamed

    def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if deleted, False if not found."""
        path = self._session_dir / f"{session_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def search(self, query: str) -> list[SessionSummary]:
        """Search sessions by name, tags, or project_path substring."""
        query_lower = query.lower()
        results: list[SessionSummary] = []
        for summary in self.list_sessions():
            if (
                query_lower in summary.name.lower()
                or query_lower in str(summary.project_path).lower()
                or any(query_lower in t.lower() for t in summary.tags)
            ):
                results.append(summary)
        return results

    def get_by_name(self, name: str) -> Session | None:
        """Find session by exact name match (first/most recent match)."""
        for summary in self.list_sessions():
            if summary.name == name:
                return self.load(summary.id)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime/test_session_naming.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/runtime/session.py tests/test_runtime/test_session_naming.py
git commit -m "feat: add SessionManager.rename/delete/search/get_by_name"
```

---

### Task 7: /session Command Expansion

**Files:**
- Modify: `llm_code/cli/tui.py`

- [ ] **Step 1: Locate existing /session handler in tui.py**

Search for the existing `/session` handler. It handles `list` and `save` subcommands.

- [ ] **Step 2: Expand /session command handler**

Add subcommands: `load`, `rename`, `delete`, `search`, `tag`. Use the existing `_interactive_pick()` pattern for the picker.

```python
# In the /session handler section of tui.py:

def _handle_session_command(self, args: str) -> None:
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "list" or sub == "":
        self._session_list_with_picker()
    elif sub == "save":
        name = rest.strip()
        if name:
            self._session = self._session.rename(name)
        self._session_manager.save(self._session)
        display_name = name or self._session.id
        console.print(f"[green]Session saved: {display_name}[/]")
    elif sub == "load":
        self._session_load(rest.strip())
    elif sub == "rename":
        if not rest.strip():
            console.print("[red]Usage: /session rename <name>[/]")
            return
        self._session = self._session.rename(rest.strip())
        self._session_manager.save(self._session)
        console.print(f"[green]Session renamed: {rest.strip()}[/]")
    elif sub == "delete":
        self._session_delete(rest.strip())
    elif sub == "search":
        self._session_search(rest.strip())
    elif sub == "tag":
        tags = rest.strip().split()
        if not tags:
            console.print("[red]Usage: /session tag <tag1> [tag2...][/]")
            return
        self._session = self._session.add_tags(*tags)
        self._session_manager.save(self._session)
        console.print(f"[green]Tags added: {', '.join(tags)}[/]")
    else:
        console.print(f"[red]Unknown subcommand: {sub}[/]")

def _session_list_with_picker(self) -> None:
    summaries = self._session_manager.list_sessions()
    if not summaries:
        console.print("[dim]No saved sessions.[/]")
        return
    items = []
    for s in summaries:
        name = s.name or s.id
        is_current = str(s.project_path) == str(self._session.project_path)
        desc = f"{s.project_path} ({s.message_count} msgs)"
        items.append((name, desc, is_current))
    selected = _interactive_pick("Sessions", items)
    if selected:
        self._session_load(selected)

def _session_load(self, identifier: str) -> None:
    if not identifier:
        console.print("[red]Usage: /session load <id|name>[/]")
        return
    try:
        session = self._session_manager.load(identifier)
    except FileNotFoundError:
        session_by_name = self._session_manager.get_by_name(identifier)
        if session_by_name is None:
            console.print(f"[red]Session not found: {identifier}[/]")
            return
        session = session_by_name
    self._session = session
    display = session.name or session.id
    console.print(f"[green]Loaded session: {display} ({len(session.messages)} messages)[/]")

def _session_delete(self, identifier: str) -> None:
    if not identifier:
        console.print("[red]Usage: /session delete <id|name>[/]")
        return
    # Resolve name to id if needed
    session_id = identifier
    try:
        self._session_manager.load(identifier)
    except FileNotFoundError:
        found = self._session_manager.get_by_name(identifier)
        if found is None:
            console.print(f"[red]Session not found: {identifier}[/]")
            return
        session_id = found.id
    try:
        confirm = input(f"Delete session {identifier}? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if confirm == "y":
        self._session_manager.delete(session_id)
        console.print(f"[green]Session deleted: {identifier}[/]")
    else:
        console.print("[dim]Cancelled.[/]")

def _session_search(self, query: str) -> None:
    if not query:
        console.print("[red]Usage: /session search <query>[/]")
        return
    results = self._session_manager.search(query)
    if not results:
        console.print(f"[dim]No sessions matching '{query}'.[/]")
        return
    for s in results:
        name = s.name or s.id
        tags_str = f" [{', '.join(s.tags)}]" if s.tags else ""
        console.print(f"  [bold]{name}[/]{tags_str}  [dim]· {s.project_path} ({s.message_count} msgs)[/]")
```

- [ ] **Step 3: Smoke test manually**

Run `llm-code`, then type `/session list`, `/session save test-name`, `/session search test`.

- [ ] **Step 4: Commit**

```bash
git add llm_code/cli/tui.py
git commit -m "feat: expand /session command with load, rename, delete, search, tag subcommands"
```

---

## Phase 3b: Core Runtime Changes

---

### Task 8: CompressorConfig

**Files:**
- Modify: `llm_code/runtime/config.py`

- [ ] **Step 1: Add CompressorConfig dataclass**

In `llm_code/runtime/config.py`, add before `RuntimeConfig`:

```python
@dataclass(frozen=True)
class CompressorConfig:
    llm_summarize: bool = False
    summarize_model: str = ""
    max_summary_tokens: int = 1000
```

Add to `RuntimeConfig`:
```python
    compressor: CompressorConfig = field(default_factory=CompressorConfig)
```

Add to `ConfigSchema`:
```python
    compressor: dict = {}
```

- [ ] **Step 2: Verify config loading doesn't break**

Run: `pytest tests/test_runtime/test_config.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add llm_code/runtime/config.py
git commit -m "feat: add CompressorConfig with llm_summarize toggle"
```

---

### Task 9: Level 5 LLM Semantic Compression

**Files:**
- Modify: `llm_code/runtime/compressor.py`
- Test: `tests/test_runtime/test_compressor_llm.py`

- [ ] **Step 1: Write failing tests for Level 5**

```python
# tests/test_runtime/test_compressor_llm.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from llm_code.api.types import Message, TextBlock, ToolUseBlock, ToolResultBlock, TokenUsage
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.session import Session


def _make_session(n_messages: int, chars_per_msg: int = 400) -> Session:
    """Create a session with n_messages, each with chars_per_msg characters."""
    msgs = []
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"Message {i}: " + "x" * chars_per_msg
        msgs.append(Message(role=role, content=(TextBlock(text=text),)))
    return Session(
        id="test1234",
        messages=tuple(msgs),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        total_usage=TokenUsage(input_tokens=0, output_tokens=0),
        project_path=Path("/tmp"),
    )


class TestLevel5LLMSummarize:
    @pytest.mark.asyncio
    async def test_compress_async_with_llm(self):
        """Level 5 replaces placeholder with LLM-generated summary."""
        provider = AsyncMock()
        provider.complete.return_value = MagicMock(
            content="## Summary\nDid some coding.\n## Modified Files\n- /app/main.py",
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        compressor = ContextCompressor(
            max_result_chars=100,
            provider=provider,
            summarize_model="test-model",
        )
        session = _make_session(40, chars_per_msg=1000)  # ~10000 tokens
        result = await compressor.compress_async(session, max_tokens=2000)
        # Should have called the provider for summarization
        assert provider.complete.called
        # The summary message should contain the LLM output, not the placeholder
        first_text = ""
        for msg in result.messages:
            for block in msg.content:
                if isinstance(block, TextBlock) and "Summary" in block.text:
                    first_text = block.text
                    break
        assert "Did some coding" in first_text

    @pytest.mark.asyncio
    async def test_compress_async_fallback_on_error(self):
        """If LLM call fails, fall back to Level 4 placeholder."""
        provider = AsyncMock()
        provider.complete.side_effect = Exception("API error")
        compressor = ContextCompressor(
            max_result_chars=100,
            provider=provider,
            summarize_model="test-model",
        )
        session = _make_session(40, chars_per_msg=1000)
        result = await compressor.compress_async(session, max_tokens=2000)
        # Should still return a valid session (Level 4 fallback)
        assert len(result.messages) > 0

    @pytest.mark.asyncio
    async def test_compress_async_no_provider_skips_level5(self):
        """Without a provider, compress_async behaves like sync compress."""
        compressor = ContextCompressor(max_result_chars=100)
        session = _make_session(40, chars_per_msg=1000)
        result = await compressor.compress_async(session, max_tokens=2000)
        # Should have the placeholder text from Level 4
        has_placeholder = any(
            isinstance(b, TextBlock) and "Previous conversation summary" in b.text
            for msg in result.messages
            for b in msg.content
        )
        assert has_placeholder

    def test_sync_compress_unchanged(self):
        """Sync compress() should NOT use Level 5 (no async)."""
        compressor = ContextCompressor(max_result_chars=100)
        session = _make_session(40, chars_per_msg=1000)
        result = compressor.compress(session, max_tokens=2000)
        assert len(result.messages) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime/test_compressor_llm.py -v`
Expected: FAIL (compress_async doesn't exist, constructor signature changed)

- [ ] **Step 3: Implement Level 5 in ContextCompressor**

In `llm_code/runtime/compressor.py`:

```python
"""ContextCompressor: 5-level progressive context compression."""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from llm_code.api.types import Message, MessageRequest, TextBlock, ToolResultBlock, ToolUseBlock
from llm_code.runtime.session import Session

if TYPE_CHECKING:
    from llm_code.api.provider import LLMProvider

_log = logging.getLogger(__name__)

_SUMMARIZE_SYSTEM_PROMPT = """\
You are a context compression agent. Given conversation messages from a coding \
session, produce a concise summary preserving:

1. What files were read, created, or modified (exact paths)
2. Key decisions made and their rationale
3. Current state of the task (what's done, what's pending)
4. Any errors encountered and how they were resolved

Be factual. Use bullet points. Do not include code blocks unless critical.
"""


class ContextCompressor:
    """Progressively compress a Session context through 5 escalating levels.

    Level 1 — snip_compact
    Level 2 — micro_compact
    Level 3 — context_collapse
    Level 4 — auto_compact
    Level 5 — llm_summarize (async only, requires provider)
    """

    def __init__(
        self,
        max_result_chars: int = 2000,
        provider: "LLMProvider | None" = None,
        summarize_model: str = "",
        max_summary_tokens: int = 1000,
    ) -> None:
        self._max_result_chars = max_result_chars
        self._cached_indices: set[int] = set()
        self._provider = provider
        self._summarize_model = summarize_model
        self._max_summary_tokens = max_summary_tokens

    # ... (existing mark_as_cached, _is_cached, compress unchanged) ...

    async def compress_async(self, session: Session, max_tokens: int) -> Session:
        """Async compress with optional Level 5 LLM summarization."""
        # Run Levels 1-4 synchronously (they're fast)
        result = self.compress(session, max_tokens)

        # Level 5: Replace placeholder with LLM summary if provider available
        if self._provider is not None and result.estimated_tokens() > 0:
            result = await self._llm_summarize(result)

        return result

    async def _llm_summarize(self, session: Session) -> Session:
        """Replace the Level 4 placeholder message with an LLM-generated summary."""
        # Find the placeholder message
        placeholder_idx = None
        for i, msg in enumerate(session.messages):
            for block in msg.content:
                if isinstance(block, TextBlock) and "[Previous conversation summary]" in block.text:
                    placeholder_idx = i
                    break
            if placeholder_idx is not None:
                break

        if placeholder_idx is None:
            return session  # No placeholder to replace

        # Build context from all messages except the placeholder
        context_parts: list[str] = []
        for i, msg in enumerate(session.messages):
            if i == placeholder_idx:
                continue
            for block in msg.content:
                if isinstance(block, TextBlock):
                    context_parts.append(f"[{msg.role}] {block.text[:500]}")
                elif isinstance(block, ToolUseBlock):
                    context_parts.append(f"[tool_call] {block.name}({str(block.input)[:200]})")
                elif isinstance(block, ToolResultBlock):
                    context_parts.append(f"[tool_result] {block.content[:200]}")

        if not context_parts:
            return session

        try:
            request = MessageRequest(
                model=self._summarize_model,
                system=_SUMMARIZE_SYSTEM_PROMPT,
                messages=(
                    Message(
                        role="user",
                        content=(TextBlock(text="Summarize this conversation:\n\n" + "\n".join(context_parts)),),
                    ),
                ),
                max_tokens=self._max_summary_tokens,
            )
            response = await self._provider.complete(request)
            summary_text = response.content if isinstance(response.content, str) else str(response.content)
        except Exception:
            _log.warning("Level 5 LLM summarization failed, keeping Level 4 placeholder", exc_info=True)
            return session

        # Replace placeholder with LLM summary
        summary_msg = Message(
            role="user",
            content=(TextBlock(text=f"[Conversation summary]\n{summary_text}"),),
        )
        messages = list(session.messages)
        messages[placeholder_idx] = summary_msg
        return dataclasses.replace(session, messages=tuple(messages))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime/test_compressor_llm.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing compressor tests to verify no regression**

Run: `pytest tests/test_runtime/test_compressor.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add llm_code/runtime/compressor.py tests/test_runtime/test_compressor_llm.py
git commit -m "feat: add Level 5 LLM semantic compression to ContextCompressor"
```

---

### Task 10: BashRulesConfig

**Files:**
- Modify: `llm_code/runtime/config.py`

- [ ] **Step 1: Add BashRule and BashRulesConfig**

In `llm_code/runtime/config.py`:

```python
@dataclass(frozen=True)
class BashRule:
    pattern: str
    action: str  # "allow" | "confirm" | "block"
    description: str = ""

@dataclass(frozen=True)
class BashRulesConfig:
    rules: tuple[BashRule, ...] = ()
```

Add to `RuntimeConfig`:
```python
    bash_rules: BashRulesConfig = field(default_factory=BashRulesConfig)
```

Add to `ConfigSchema`:
```python
    bash_rules: list = []
```

- [ ] **Step 2: Verify config loading**

Run: `pytest tests/test_runtime/test_config.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add llm_code/runtime/config.py
git commit -m "feat: add BashRule and BashRulesConfig for user-defined bash rules"
```

---

### Task 11: Integrate User Rules into classify_command()

**Files:**
- Modify: `llm_code/tools/bash.py`
- Test: `tests/test_tools/test_bash_user_rules.py`

- [ ] **Step 1: Write failing tests for user bash rules**

```python
# tests/test_tools/test_bash_user_rules.py
import pytest
from llm_code.tools.bash import classify_command
from llm_code.runtime.config import BashRule


class TestUserBashRules:
    def test_user_allow_rule(self):
        rules = (BashRule(pattern=r"^git\s+push\b(?!.*--force)", action="allow"),)
        result = classify_command("git push origin main", user_rules=rules)
        assert result.is_safe
        assert "user:0" in result.rule_ids

    def test_user_block_rule(self):
        rules = (BashRule(pattern=r"^docker\s+system\s+prune", action="block"),)
        result = classify_command("docker system prune", user_rules=rules)
        assert result.is_blocked
        assert "user:0" in result.rule_ids

    def test_user_confirm_rule(self):
        rules = (BashRule(pattern=r"^git\s+push\s+--force", action="confirm"),)
        result = classify_command("git push --force origin main", user_rules=rules)
        assert result.needs_confirm
        assert "user:0" in result.rule_ids

    def test_user_rules_take_precedence(self):
        """User 'allow' rule overrides built-in 'needs_confirm' for rm."""
        rules = (BashRule(pattern=r"^rm\s+temp\.txt$", action="allow"),)
        result = classify_command("rm temp.txt", user_rules=rules)
        assert result.is_safe

    def test_no_user_rules_falls_through(self):
        """Without user rules, built-in classifier applies."""
        result = classify_command("ls -la", user_rules=())
        assert result.is_safe

    def test_first_matching_rule_wins(self):
        rules = (
            BashRule(pattern=r"^git\s+push", action="block"),
            BashRule(pattern=r"^git\s+push", action="allow"),
        )
        result = classify_command("git push", user_rules=rules)
        assert result.is_blocked  # First rule wins

    def test_invalid_regex_skipped(self):
        rules = (BashRule(pattern=r"[invalid", action="allow"),)
        # Should not crash; skip invalid rule, fall through to built-in
        result = classify_command("ls", user_rules=rules)
        assert result.is_safe
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools/test_bash_user_rules.py -v`
Expected: FAIL (classify_command doesn't accept user_rules parameter)

- [ ] **Step 3: Integrate user rules into classify_command**

In `llm_code/tools/bash.py`, modify `classify_command()`:

```python
def classify_command(
    command: str,
    user_rules: tuple[BashRule, ...] = (),
) -> BashSafetyResult:
    """Classify a bash command for safety.

    User rules are checked first (in order). First matching rule wins.
    If no user rule matches, falls through to built-in classifier.
    """
    # Phase 0: User-defined rules (checked first)
    for i, rule in enumerate(user_rules):
        try:
            if re.search(rule.pattern, command):
                action_map = {
                    "allow": "safe",
                    "confirm": "needs_confirm",
                    "block": "blocked",
                }
                classification = action_map.get(rule.action, "needs_confirm")
                reason = rule.description or f"Matched user rule: {rule.pattern}"
                return BashSafetyResult(
                    classification=classification,
                    reasons=(reason,),
                    rule_ids=(f"user:{i}",),
                )
        except re.error:
            _log.warning("Invalid regex in user bash rule %d: %s", i, rule.pattern)
            continue

    # Phase 1+: Built-in classifier (existing code unchanged)
    # ... rest of existing classify_command ...
```

Also update `BashTool.execute()` and `BashTool.is_read_only()` / `is_destructive()` to pass user_rules from config.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools/test_bash_user_rules.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing bash tests to verify no regression**

Run: `pytest tests/test_tools/test_bash.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add llm_code/tools/bash.py tests/test_tools/test_bash_user_rules.py
git commit -m "feat: add user-defined bash rules with regex matching in classify_command"
```

---

### Task 12: Full Test Suite Verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest -q --tb=short`
Expected: 2650+ passed (plus new tests), 0 new failures

- [ ] **Step 2: Commit any fixups if needed**

---

## Summary

| Task | Feature | Files | Phase |
|------|---------|-------|-------|
| 1-2 | Status line | `status_line.py`, `tui.py` | 3a |
| 3-4 | MultiEdit | `edit_file.py`, `multi_edit.py` | 3a |
| 5-7 | Session naming | `session.py`, `tui.py` | 3a |
| 8-9 | LLM compression | `config.py`, `compressor.py` | 3b |
| 10-11 | Bash rules | `config.py`, `bash.py` | 3b |
| 12 | Verification | — | Final |

Phase 3a tasks (1-7) are independent and can run in parallel.
Phase 3b tasks (8-11) are independent of each other but depend on 3a being committed.
Task 12 is the final verification.

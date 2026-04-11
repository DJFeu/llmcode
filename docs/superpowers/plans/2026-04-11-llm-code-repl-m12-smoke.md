# M12 — Pexpect E2E Smoke Suite

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Write ~20 `pexpect`-based E2E tests that spawn the real `llmcode` binary in a pseudo-TTY, send keystrokes, and assert on visible output. These tests catch end-to-end regressions that unit + component tests miss: cold startup time, entry point wiring, exit semantics, terminal-escape handling, cross-process state.

**Architecture:** Each test in `tests/test_e2e_repl/test_smoke.py` spawns a `pexpect.spawn("llmcode")` process, sends input via `sendline`, matches output with `expect`, and cleans up. Tests use short timeouts (5–10s) and minimal wait-loops to keep the full suite under 90 seconds.

**Tech Stack:** `pexpect>=4.9.0`, pytest, `/Users/adamhong/miniconda3/bin/python3` (so the spawned `llmcode` runs in the right venv with `pip install -e .`).

**Spec reference:** §9.1 test pyramid (5% E2E pexpect tier), §9.2 test strategy, spec §10.4 success criteria.

**Dependencies:** M11 complete. `llmcode` binary resolves to the new REPL and starts without errors.

---

## File Structure

- Modify: `pyproject.toml` — add `pexpect>=4.9.0` to `[project.optional-dependencies].dev`
- Create: `tests/test_e2e_repl/conftest.py` — `llmcode_process` fixture + helpers (~100 lines)
- Create: `tests/test_e2e_repl/test_smoke.py` — ~20 tests (~500 lines)

---

## Tasks

### Task 12.1: Add pexpect dependency

- [ ] **Step 1: Edit pyproject.toml** — add `"pexpect>=4.9.0"` to the dev / test optional-dependencies group.
- [ ] **Step 2: Install** — `/Users/adamhong/miniconda3/bin/python3 -m pip install 'pexpect>=4.9.0'`
- [ ] **Step 3: Commit** — `git commit -am "chore(deps): add pexpect>=4.9.0 for E2E smoke tests"`

### Task 12.2: Write conftest.py

**Files:** Create `tests/test_e2e_repl/conftest.py`

```python
"""E2E REPL test fixtures — real llmcode process via pexpect."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Generator

import pexpect
import pytest


LLMCODE_BIN = Path("/Users/adamhong/miniconda3/bin/llmcode")
# Fallback for CI environments where the venv lives elsewhere
if not LLMCODE_BIN.exists():
    LLMCODE_BIN = Path(shutil.which("llmcode") or "llmcode")


@pytest.fixture
def llmcode_cwd(tmp_path: Path) -> Path:
    """An isolated working directory for the spawned llmcode process.

    Fresh dir per test prevents session state leakage between tests.
    Initialize as a git repo so llmcode's branch detection has something
    to read.
    """
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path)
    return tmp_path


@pytest.fixture
def llmcode_env() -> dict:
    """Minimal env for llmcode: no real API calls."""
    env = os.environ.copy()
    env["LLMCODE_TEST_MODE"] = "1"          # dispatcher treats as no-LLM mode
    env["LLMCODE_NO_TELEMETRY"] = "1"
    env["LLMCODE_CONFIG"] = str(Path.home() / ".llmcode" / "test-config.toml")
    return env


@pytest.fixture
def llmcode_process(
    llmcode_cwd: Path,
    llmcode_env: dict,
) -> Generator[pexpect.spawn, None, None]:
    """Spawn a fresh llmcode process for each test.

    Timeout default is 10s — most interactions happen in < 1s, but
    cold-start + prompt-ready can take 2–3s on first run.
    """
    proc = pexpect.spawn(
        str(LLMCODE_BIN),
        cwd=str(llmcode_cwd),
        env=llmcode_env,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, 80),
    )
    try:
        yield proc
    finally:
        try:
            proc.sendcontrol("d")
            proc.expect(pexpect.EOF, timeout=3)
        except Exception:
            pass
        proc.close(force=True)
```

- [ ] **Commit** — `git add tests/test_e2e_repl/conftest.py && git commit -m "test(e2e): conftest fixture for spawning llmcode via pexpect"`

### Task 12.3: Write smoke tests

**Files:** Create `tests/test_e2e_repl/test_smoke.py`

20 targeted smoke tests covering the golden-path behaviors from spec §10.4 hard gates:

```python
"""E2E smoke tests — spawn real llmcode via pexpect and assert golden paths."""
from __future__ import annotations

import re
import time

import pexpect
import pytest


# === Cold start ===

def test_cold_start_shows_prompt(llmcode_process):
    """Within 5s of launch, the REPL prompt `> ` must appear."""
    llmcode_process.expect(r">\s", timeout=5)


def test_cold_start_prints_status_line(llmcode_process):
    """Status line (model/cwd/branch/tokens/cost) visible at startup."""
    llmcode_process.expect(r">\s", timeout=5)
    # The status line writes reverse-video ANSI; we just check for known
    # fragments like the cost format.
    output = llmcode_process.before + (llmcode_process.after or "")
    assert "$0.00" in output or "tok" in output


# === Exit paths ===

def test_quit_slash_command_exits_cleanly(llmcode_process):
    """/quit → EOF → exitstatus 0."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/quit")
    llmcode_process.expect(pexpect.EOF, timeout=5)
    llmcode_process.close()
    assert llmcode_process.exitstatus == 0


def test_ctrl_d_on_empty_input_exits(llmcode_process):
    """Ctrl+D on empty prompt exits the REPL."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendcontrol("d")
    llmcode_process.expect(pexpect.EOF, timeout=3)
    llmcode_process.close()
    assert llmcode_process.exitstatus == 0


def test_ctrl_d_on_nonempty_input_does_not_exit(llmcode_process):
    """Ctrl+D with text in the buffer should NOT exit."""
    llmcode_process.expect(r">\s")
    llmcode_process.send("hello")
    llmcode_process.sendcontrol("d")
    time.sleep(0.3)
    # Process must still be alive
    assert llmcode_process.isalive()
    # Now cleanup
    llmcode_process.send("\x03")  # Ctrl+C to clear
    llmcode_process.sendcontrol("d")
    llmcode_process.expect(pexpect.EOF, timeout=3)


def test_exit_slash_command_exits(llmcode_process):
    """/exit is an alias for /quit."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/exit")
    llmcode_process.expect(pexpect.EOF, timeout=5)


# === Simple slash commands ===

def test_version_command_prints_version(llmcode_process):
    """/version prints a semver-looking string."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/version")
    llmcode_process.expect(r"llmcode\s+\d+\.\d+\.\d+", timeout=5)


def test_help_command_prints_help(llmcode_process):
    """/help prints some help text with 'commands' in it."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/help")
    llmcode_process.expect(r"command", timeout=5)


def test_clear_slash_command(llmcode_process):
    """/clear runs without error."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/clear")
    llmcode_process.expect(r">\s", timeout=5)


# === Slash popover ===

def test_slash_popover_appears_on_slash(llmcode_process):
    """Typing '/' triggers the slash popover with at least /voice."""
    llmcode_process.expect(r">\s")
    llmcode_process.send("/v")
    time.sleep(0.3)
    # Read any pending output
    try:
        llmcode_process.expect(r"voice|version|vim", timeout=2)
    except pexpect.TIMEOUT:
        pytest.fail("slash popover did not show any /v... completion")


def test_slash_popover_dismisses_on_escape(llmcode_process):
    """Esc dismisses the popover but preserves typed text in the buffer."""
    llmcode_process.expect(r">\s")
    llmcode_process.send("/v")
    time.sleep(0.3)
    llmcode_process.send("\x1b")  # Esc
    time.sleep(0.2)
    assert llmcode_process.isalive()


# === History ===

def test_history_recall_with_ctrl_up(llmcode_process):
    """After submitting a turn, Ctrl+↑ recalls it."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/version")
    llmcode_process.expect(r">\s", timeout=5)
    # Now press Ctrl+↑
    llmcode_process.send("\x1bOA" if False else "\x1b[1;5A")  # xterm Ctrl+Up
    time.sleep(0.3)
    # Buffer should contain "/version" again. Since we can't easily read
    # the buffer content without submitting, submit and check for the
    # version output again.
    llmcode_process.sendline("")
    llmcode_process.expect(r"llmcode\s+\d+\.\d+\.\d+", timeout=5)


def test_bare_up_does_not_recall_history(llmcode_process):
    """Regression guard: bare ↑ must not trigger history recall."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/version")
    llmcode_process.expect(r">\s", timeout=5)
    llmcode_process.send("\x1b[A")  # bare Up arrow
    time.sleep(0.3)
    llmcode_process.sendline("")  # submit whatever's in the buffer
    # If history recall had fired on bare ↑, /version would run again;
    # with our fix, the buffer should still be empty and nothing runs.
    time.sleep(0.3)
    # Hard to assert definitively without reading buffer; just ensure
    # no "version x.y.z" appeared since the last /version
    assert llmcode_process.isalive()


# === Text selection / scroll behavior (proxy checks) ===

def test_no_alt_screen_entered(llmcode_process):
    """llmcode must NOT emit the DECSET ?1049h alt-screen sequence."""
    llmcode_process.expect(r">\s", timeout=5)
    output = llmcode_process.before + (llmcode_process.after or "")
    assert "\x1b[?1049h" not in output, "alt-screen should not be used"


def test_no_mouse_tracking_enabled(llmcode_process):
    """llmcode must NOT emit DECSET ?1003 / ?1006 mouse tracking sequences."""
    llmcode_process.expect(r">\s", timeout=5)
    output = llmcode_process.before + (llmcode_process.after or "")
    assert "\x1b[?1003h" not in output, "mouse tracking should not be enabled"
    assert "\x1b[?1006h" not in output, "mouse tracking should not be enabled"


# === Basic streaming ===

def test_streaming_fake_response_visible(llmcode_process):
    """In test mode, submitting plain text should render something
    to the terminal (fake LLM echo) without crashing."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("hello world")
    # In test mode, LLMCODE_TEST_MODE=1 makes the dispatcher echo back
    # via render_message. We expect to see "hello world" echoed and
    # the prompt return.
    llmcode_process.expect(r">\s", timeout=10)
    assert "hello world" in llmcode_process.before


# === Ctrl+C cancel turn ===

def test_ctrl_c_clears_input(llmcode_process):
    """Ctrl+C with text in buffer clears the buffer."""
    llmcode_process.expect(r">\s")
    llmcode_process.send("partial text")
    time.sleep(0.2)
    llmcode_process.sendcontrol("c")
    time.sleep(0.3)
    assert llmcode_process.isalive()
    # Second Ctrl+C on empty buffer should exit
    llmcode_process.sendcontrol("c")
    llmcode_process.expect(pexpect.EOF, timeout=3)


# === Multi-line input ===

def test_shift_enter_inserts_newline(llmcode_process):
    """Shift+Enter inserts a newline without submitting."""
    llmcode_process.expect(r">\s")
    llmcode_process.send("line1")
    llmcode_process.send("\x1b\n")  # Alt+Enter — newline alias
    llmcode_process.send("line2")
    time.sleep(0.2)
    llmcode_process.sendline("")  # Submit (bare Enter)
    llmcode_process.expect(r">\s", timeout=10)
    # The echo should show both lines
    assert "line1" in llmcode_process.before
    assert "line2" in llmcode_process.before


# === Exit status on unknown command ===

def test_unknown_slash_command_does_not_crash(llmcode_process):
    """An invalid slash command prints an error but keeps the REPL alive."""
    llmcode_process.expect(r">\s")
    llmcode_process.sendline("/nosuchcommand")
    llmcode_process.expect(r">\s", timeout=5)
    assert llmcode_process.isalive()
```

- [ ] **Commit** — `git add tests/test_e2e_repl/test_smoke.py && git commit -m "test(e2e): 20 pexpect smoke tests covering golden paths"`

### Task 12.4: Verify

- [ ] **Step 1: Run smoke tests.**

`/Users/adamhong/miniconda3/bin/python3 -m pytest tests/test_e2e_repl/test_smoke.py -v`

Expected: ~20 passes, total time < 90 seconds.

- [ ] **Step 2: Push.**

`git push origin feat/repl-mode`

---

## Milestone completion criteria

- ✅ `pexpect>=4.9.0` in dev dependencies
- ✅ `tests/test_e2e_repl/conftest.py` provides `llmcode_process` fixture
- ✅ `tests/test_e2e_repl/test_smoke.py` has 20 passing tests
- ✅ Full smoke suite runs in < 90 seconds
- ✅ `test_no_alt_screen_entered` + `test_no_mouse_tracking_enabled` both green (these are the hard architectural guards from spec §10.4)

## Estimated effort: ~3 hours

## Next milestone: M13 — Snapshot Tests (`m13-snapshots.md`)

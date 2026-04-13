"""E2E REPL test fixtures — real llmcode process via pexpect.

M12 deliverable. Fixtures to spawn an actual llmcode REPL in a
pseudo-TTY so the smoke suite can drive it with real keystrokes and
capture real terminal output.

Portability notes (from M11-M14 audit §M12 M3):
- We prefer ``sys.executable -m llm_code.cli.main`` over a hard-coded
  binary path so the test works in any venv and on CI. This avoids
  the "is llmcode on PATH?" question entirely and always runs the
  in-tree module under the active Python interpreter.
- A ``llmcode`` binary on PATH is also accepted as a fallback for
  users who want to exercise the installed entry point explicitly
  via the ``LLMCODE_SMOKE_BIN`` env var. This is off by default —
  the module-as-script form is what tests target.

Test-mode (M12 audit §H3):
- Every fixture sets ``LLMCODE_TEST_MODE=1`` in the child env so
  plain-text submissions echo back as ``echo: <text>`` instead of
  calling a real LLM provider. Slash commands still route through
  the real dispatcher — that's the whole point of the smoke tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Generator

import pexpect
import pytest


def _resolve_llmcode_launcher() -> list[str]:
    """Return the argv prefix that launches llmcode.

    Defaults to ``sys.executable -m llm_code.cli.main`` so smoke tests
    always run the in-tree code under the active interpreter — even
    when a stale ``pip install -e`` entry point still points at
    ``cli.tui_main`` (which M11.2 deleted). Set ``LLMCODE_SMOKE_BIN``
    to force a specific binary path.
    """
    override = os.environ.get("LLMCODE_SMOKE_BIN")
    if override:
        return [override]
    return [sys.executable, "-m", "llm_code.cli.main"]


@pytest.fixture
def llmcode_launcher() -> list[str]:
    """The argv prefix used to spawn llmcode under test."""
    return _resolve_llmcode_launcher()


@pytest.fixture
def llmcode_cwd(tmp_path: Path) -> Path:
    """Isolated working directory per test.

    Initialized as a git repo so the runtime's branch / status
    detection has something to read and doesn't error on bare dirs.
    """
    subprocess.run(
        ["git", "init", "-q"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"],
        cwd=tmp_path, check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=tmp_path, check=False,
    )
    return tmp_path


@pytest.fixture
def llmcode_env() -> dict:
    """Minimal env — no real API calls, fake test config path."""
    env = os.environ.copy()
    # Strip PYTHONPATH/PYTHONHOME so a parent shell with a user
    # site-packages override (e.g. ~/Library/Python/3.9/...) can't
    # pollute the child interpreter's sys.path and make it load
    # incompatible pydantic/click from a different Python version.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["LLMCODE_TEST_MODE"] = "1"
    # Best-effort hint that telemetry should be off even if a real
    # Telemetry client gets instantiated. No code actually reads this
    # env var today but setting it costs nothing and documents intent.
    env["LLMCODE_NO_TELEMETRY"] = "1"
    # Point at an empty config so we don't load the user's real
    # ~/.llmcode/config.json during smoke testing.
    env["LLMCODE_CONFIG"] = str(
        Path.home() / ".llmcode" / "test-config.toml"
    )
    # prompt_toolkit honors TERM when deciding which capabilities to
    # emit. A plain xterm-256color keeps the output readable under
    # pexpect's pseudo-TTY.
    env.setdefault("TERM", "xterm-256color")
    return env


def send_line(proc: "pexpect.spawn", text: str) -> None:
    """Send a line of input terminated by Carriage Return.

    prompt_toolkit distinguishes ``\\r`` (Enter/submit) from ``\\n``
    (multi-line newline). ``pexpect.spawn.sendline`` uses ``os.linesep``
    which is ``\\n`` on Unix — that inserts a newline instead of
    submitting. Use this helper in tests to submit a command cleanly.

    Calibrated during the M12 pexpect baseline capture (audit §H4).
    """
    proc.send(text + "\r")


def wait_ready(proc: "pexpect.spawn", cold_start_seconds: float = 3.0) -> None:
    """Wait for the REPL's cold-start sequence to finish.

    The v2.0.0 REPL doesn't emit a ``>`` prompt per line — its input
    area is rendered by prompt_toolkit at the bottom of the terminal.
    We can't ``expect("> ")``; instead we sleep a generous window to
    let the welcome banner + status line + input area settle, then
    drain any pending PT drawing commands from the buffer.

    3.0s default was calibrated for patch_stdout + welcome banner
    + cold model profile import; dropped below that and tests race
    with PT's initial layout render under pexpect's pseudo-TTY.
    """
    import time
    time.sleep(cold_start_seconds)
    try:
        proc.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=0.5)
    except Exception:
        pass


def capture(proc: "pexpect.spawn", wait: float = 0.6) -> str:
    """Wait briefly and return whatever's currently in the pexpect buffer.

    Used after submitting a command to let the REPL's print calls land
    before asserting on them. The 0.6s default is enough for a single
    command to dispatch, render, and drain through ``patch_stdout``'s
    PT redraw cycle; longer commands (e.g. /help) can pass an explicit
    ``wait=0.8`` or higher.
    """
    import time
    time.sleep(wait)
    try:
        proc.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=0.5)
    except Exception:
        pass
    return proc.before or ""


@pytest.fixture
def llmcode_process(
    llmcode_launcher: list[str],
    llmcode_cwd: Path,
    llmcode_env: dict,
) -> Generator[pexpect.spawn, None, None]:
    """Spawn a fresh llmcode process for each test.

    Uses the ``sys.executable -m`` form when no installed binary is
    found (M12 audit §M3 fix). Timeout default is 10s — most
    interactions complete in well under a second, but cold start +
    prompt-ready can take 2–3s on first run.
    """
    argv = llmcode_launcher
    proc = pexpect.spawn(
        argv[0],
        args=argv[1:],
        cwd=str(llmcode_cwd),
        env=llmcode_env,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, 80),
        # Raise from the default 2 KB so long /help output with all
        # registered commands + skill commands fits in one read. The
        # slash-command registry plus skills routinely produce 10 KB+
        # of text, and the default buffer discards earlier fragments.
        maxread=32768,
        searchwindowsize=32768,
    )
    try:
        yield proc
    finally:
        # Best-effort clean exit: Ctrl+D first, then force close.
        try:
            if proc.isalive():
                proc.sendcontrol("d")
                proc.expect(pexpect.EOF, timeout=3)
        except Exception:
            pass
        try:
            proc.close(force=True)
        except Exception:
            pass

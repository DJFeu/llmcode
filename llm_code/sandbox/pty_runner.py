"""PTY-based command runner for interactive programs.

Uses ``ptyprocess`` to spawn commands in a real pseudo-terminal,
enabling programs that require a TTY (e.g. ``git rebase -i``,
``python -i``, curses-based tools) to work correctly.

The runner captures output via a ``pyte`` virtual terminal emulator
so the final screen state can be returned as text even for programs
that use cursor movement, colors, and screen clearing.

Falls back gracefully if ``pyte`` is not installed — raw output
is returned instead of the rendered screen.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class PTYResult:
    """Result from a PTY command execution."""

    output: str
    returncode: int
    timed_out: bool = False


def run_pty(
    command: str,
    *,
    timeout: int = 30,
    cols: int = 120,
    rows: int = 40,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> PTYResult:
    """Run a command in a PTY and capture its output.

    Uses ``ptyprocess.PtyProcessUnicode`` for the pseudo-terminal
    and optionally ``pyte`` for screen rendering. If the command
    completes before ``timeout``, the output is returned immediately.
    """
    try:
        from ptyprocess import PtyProcessUnicode
    except ImportError:
        return PTYResult(
            output="ptyprocess not installed — PTY mode unavailable",
            returncode=1,
        )

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    merged_env["TERM"] = "xterm-256color"
    merged_env["COLUMNS"] = str(cols)
    merged_env["LINES"] = str(rows)

    try:
        proc = PtyProcessUnicode.spawn(
            ["sh", "-c", command],
            dimensions=(rows, cols),
            env=merged_env,
            cwd=cwd,
        )
    except Exception as exc:
        return PTYResult(output=f"PTY spawn failed: {exc}", returncode=1)

    # Collect output with timeout
    output_chunks: list[str] = []
    deadline = time.monotonic() + timeout

    try:
        while proc.isalive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.terminate(force=True)
                return PTYResult(
                    output="".join(output_chunks),
                    returncode=124,
                    timed_out=True,
                )
            try:
                chunk = proc.read(4096)
                if chunk:
                    output_chunks.append(chunk)
            except EOFError:
                break
            except Exception:
                break
    finally:
        if proc.isalive():
            proc.terminate(force=True)

    # Try to render through pyte for clean screen output
    raw_output = "".join(output_chunks)
    rendered = _render_with_pyte(raw_output, cols, rows)

    return PTYResult(
        output=rendered,
        returncode=proc.exitstatus or 0,
    )


def _render_with_pyte(raw: str, cols: int, rows: int) -> str:
    """Render raw terminal output through pyte to get clean text.

    Falls back to raw output (with ANSI stripped) if pyte is unavailable.
    """
    try:
        import pyte
        screen = pyte.Screen(cols, rows)
        stream = pyte.Stream(screen)
        stream.feed(raw)
        # Extract non-empty lines from the screen
        lines = []
        for row in range(rows):
            line = screen.display[row].rstrip()
            lines.append(line)
        # Trim trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines) if lines else raw
    except ImportError:
        # Strip ANSI escape codes as best-effort
        import re
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw)
    except Exception:
        return raw

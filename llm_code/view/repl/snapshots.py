"""Snapshot test helper for visual regression coverage.

M13 deliverable. Simpler than syrupy / pytest-snapshot — just enough
for ~20 golden text files. Tests render a component to a StringIO
``Console`` (or flatten a ``FormattedText``), pass the captured output
through :func:`normalize` to strip non-deterministic content
(timestamps, elapsed seconds, absolute home paths), and compare
against a committed golden file under
``tests/test_view/snapshots/``.

Usage::

    from llm_code.view.repl.snapshots import capture, assert_snapshot

    def test_status_line_default_snapshot():
        output = capture(lambda console: status_line.render_to(console))
        assert_snapshot("status_line_default", output)

Regenerate a single golden::

    PYTEST_SNAPSHOT_UPDATE=1 pytest tests/test_view/test_snapshots.py::test_X

Regenerate every golden::

    PYTEST_SNAPSHOT_UPDATE=1 pytest tests/test_view/test_snapshots.py
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Callable

from rich.console import Console

# Golden files live in tests/test_view/snapshots/ regardless of which
# directory the test runs from. The path is computed relative to this
# module so ``pytest`` invocations from subdirectories still resolve
# correctly.
SNAPSHOT_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests" / "test_view" / "snapshots"
)
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def capture(
    render_fn: Callable[[Console], None],
    *,
    width: int = 80,
) -> str:
    """Render via a captured Rich ``Console`` and return the output.

    Uses ``force_terminal=True`` with ``truecolor`` so Rich emits its
    ANSI styling sequences — that's deliberate, we WANT the styling
    to be part of the snapshot so a color regression is caught.
    ``width`` is fixed at 80 so snapshots stay stable across terminal
    widths.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        width=width,
        record=False,
    )
    render_fn(console)
    return buf.getvalue()


def normalize(text: str) -> str:
    """Strip non-deterministic content before comparison.

    Substitutions (in order):

    1. ``0.3s``, ``1.2s``, ``10.5s`` → ``{elapsed}``
    2. ``2026-04-11 14:23`` → ``{date}``
    3. ``/Users/<user>/...`` → ``{home}/...``
    4. ``/home/<user>/...`` → ``{home}/...``

    Token counts (``123 tok``) are intentionally kept literal because
    they're deterministic inputs in snapshot tests — a mismatch there
    is a regression worth catching.
    """
    text = re.sub(r"\b\d+\.\d+s\b", "{elapsed}", text)
    text = re.sub(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?",
        "{date}",
        text,
    )
    text = re.sub(r"/Users/[^/\s]+", "{home}", text)
    text = re.sub(r"/home/[^/\s]+", "{home}", text)
    return text


def render_formatted(ft) -> str:
    """Flatten a ``FormattedText`` into a plain string.

    Drops the style tuples — snapshot goldens for ``FormattedText``
    components are text-only so comparisons survive style-class
    renames. Coverage for color / attribute regressions comes from
    the Rich ``capture`` path instead.
    """
    return "".join(seg[1] for seg in ft)


def _golden_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name}.txt"


def assert_snapshot(name: str, output: str) -> None:
    """Assert the captured output matches the committed golden.

    If ``PYTEST_SNAPSHOT_UPDATE=1`` is set, writes the output as the
    new golden instead of comparing. On a missing golden (first run
    without the env var), writes the file and raises so the test
    fails once — re-run verifies the new golden is stable.
    """
    normalized = normalize(output)
    golden_file = _golden_path(name)

    if os.environ.get("PYTEST_SNAPSHOT_UPDATE") == "1":
        golden_file.write_text(normalized, encoding="utf-8")
        return

    if not golden_file.exists():
        golden_file.write_text(normalized, encoding="utf-8")
        raise AssertionError(
            f"Snapshot {name!r} did not exist — created at "
            f"{golden_file}. Re-run the test to verify."
        )

    expected = golden_file.read_text(encoding="utf-8")
    if expected != normalized:
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
            "If this change is intentional, re-run with "
            "PYTEST_SNAPSHOT_UPDATE=1 to regenerate the golden."
        )


__all__ = [
    "SNAPSHOT_DIR",
    "assert_snapshot",
    "capture",
    "normalize",
    "render_formatted",
]

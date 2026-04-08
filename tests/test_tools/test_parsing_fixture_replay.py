"""Replay every captured Hermes variant in fixtures/hermes_captures/.

Each .txt file in that directory is verbatim model output that
triggered (or could trigger) a parser regression. Adding a new file
automatically extends this test — no edits required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.tools.parsing import parse_tool_calls

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "hermes_captures"


def _discover_captures() -> list[tuple[str, Path]]:
    """Return a list of (id, path) for parametrize."""
    if not _FIXTURE_DIR.exists():
        return []
    return sorted(
        (p.stem, p) for p in _FIXTURE_DIR.glob("*.txt")
    )


@pytest.mark.parametrize("capture_id,capture_path", _discover_captures())
def test_real_capture_parses_at_least_one_tool_call(
    capture_id: str, capture_path: Path
) -> None:
    """Every real capture must produce at least one ParsedToolCall with
    a non-empty name. This is the floor — individual variant tests in
    test_parsing.py cover args and other details. The point of this
    test is 'the parser can never silently drop a real production
    capture again'."""
    text = capture_path.read_text(encoding="utf-8")
    result = parse_tool_calls(text, None)
    assert len(result) >= 1, (
        f"Capture {capture_id!r} produced 0 parsed calls. "
        f"Raw text:\n{text!r}"
    )
    assert result[0].name, (
        f"Capture {capture_id!r} produced a call with empty name"
    )


def test_fixture_directory_is_not_empty() -> None:
    """If the fixture directory is empty, the parametrized test above
    silently passes (no parametrize cases). This guards against that."""
    captures = _discover_captures()
    assert len(captures) >= 3, (
        f"Expected at least 3 fixture captures, found {len(captures)}. "
        f"Captures must live in {_FIXTURE_DIR}."
    )

"""E2E: `/export` round-trip — populate a fake session, run the
command through the dispatcher, assert the file exists and contains
the expected content."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _fake_session(messages):
    """Build a Session-like object good enough for _render_session_markdown."""
    return SimpleNamespace(
        id="e2e1234",
        name="",
        project_path=Path("/tmp/project"),
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T06:00:00+00:00",
        tags=(),
        messages=tuple(messages),
    )


async def test_export_writes_file_via_dispatcher(pilot_app, tmp_path):
    from unittest.mock import MagicMock

    from llm_code.api.types import Message, TextBlock, ThinkingBlock, ToolUseBlock

    app, pilot = pilot_app
    # Install a mock runtime with a non-empty session.
    app._cwd = tmp_path
    app._runtime = MagicMock()
    app._runtime.session = _fake_session([
        Message(role="user", content=(TextBlock(text="hello e2e"),)),
        Message(role="assistant", content=(
            ThinkingBlock(content="plan the reply"),
            TextBlock(text="reply from assistant"),
        )),
        Message(role="assistant", content=(
            ToolUseBlock(id="t1", name="bash", input={"command": "ls -l"}),
        )),
    ])

    out = tmp_path / "conversation.md"
    app._cmd_dispatcher.dispatch("export", str(out))
    await pilot.pause()

    assert out.exists()
    body = out.read_text(encoding="utf-8")
    # User + assistant text blocks rendered.
    assert "hello e2e" in body
    assert "reply from assistant" in body
    # Thinking block inside collapsible <details>.
    assert "<details><summary>💭 thinking</summary>" in body
    assert "plan the reply" in body
    # Tool call serialized with the JSON input.
    assert "🔧 tool call" in body
    assert '"command": "ls -l"' in body


async def test_export_default_filename_includes_session_id(pilot_app, tmp_path):
    """Running `/export` without an argument creates
    ``llmcode-export-<session_id>-<timestamp>.md`` in the cwd."""
    from unittest.mock import MagicMock

    from llm_code.api.types import Message, TextBlock

    app, pilot = pilot_app
    app._cwd = tmp_path
    app._runtime = MagicMock()
    app._runtime.session = _fake_session([
        Message(role="user", content=(TextBlock(text="x"),)),
    ])

    app._cmd_dispatcher.dispatch("export", "")
    await pilot.pause()

    matches = list(tmp_path.glob("llmcode-export-e2e1234-*.md"))
    assert len(matches) == 1
    assert "x" in matches[0].read_text(encoding="utf-8")


async def test_export_empty_session_skipped(pilot_app, tmp_path):
    """Running `/export` with no messages should not create a file."""
    from unittest.mock import MagicMock

    app, pilot = pilot_app
    app._cwd = tmp_path
    app._runtime = MagicMock()
    app._runtime.session = _fake_session([])

    app._cmd_dispatcher.dispatch("export", "")
    await pilot.pause()

    assert list(tmp_path.glob("*.md")) == []

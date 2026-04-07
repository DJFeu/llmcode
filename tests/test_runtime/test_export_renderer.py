"""Tests for streaming markdown conversation export."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.export_renderer import (
    default_export_path,
    export_session_streaming,
    render_message_to_markdown,
)


class TestRenderMessage:
    def test_user_text(self) -> None:
        out = render_message_to_markdown({"role": "user", "content": "hello"})
        assert "## User" in out
        assert "hello" in out

    def test_assistant_text(self) -> None:
        out = render_message_to_markdown({"role": "assistant", "content": "hi"})
        assert "## Assistant" in out
        assert "hi" in out

    def test_tool_with_name(self) -> None:
        out = render_message_to_markdown(
            {"role": "tool", "tool_name": "read_file", "content": "body"}
        )
        assert "read_file" in out
        assert "body" in out

    def test_blocks_content(self) -> None:
        out = render_message_to_markdown({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
            ],
        })
        assert "first" in out
        assert "tool_use:bash" in out

    def test_timestamp_rendered(self) -> None:
        out = render_message_to_markdown(
            {"role": "user", "content": "x", "timestamp": "2026-04-07T12:00:00"}
        )
        assert "2026-04-07T12:00:00" in out


class TestExportStreaming:
    def test_exports_all_messages(self, tmp_path: Path) -> None:
        messages = [
            {"role": "user", "content": f"msg {i}"} for i in range(120)
        ]
        out = tmp_path / "session.md"
        count = export_session_streaming(messages, out, chunk_size=25)
        assert count == 120
        text = out.read_text(encoding="utf-8")
        assert "msg 0" in text
        assert "msg 119" in text
        assert text.count("## User") == 120

    def test_streaming_matches_in_memory(self, tmp_path: Path) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "tool", "tool_name": "read_file", "content": "c"},
        ]
        out = tmp_path / "s.md"
        export_session_streaming(messages, out, chunk_size=1, header="# Test")
        written = out.read_text(encoding="utf-8")

        in_memory = "# Test\n\n" + "".join(
            render_message_to_markdown(m) + "\n" for m in messages
        )
        assert written == in_memory

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "dir" / "s.md"
        export_session_streaming([{"role": "user", "content": "x"}], out)
        assert out.exists()

    def test_empty_messages(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.md"
        count = export_session_streaming([], out)
        assert count == 0
        assert out.exists()

    def test_invalid_chunk_size(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            export_session_streaming([], tmp_path / "x.md", chunk_size=0)


def test_default_export_path_shape() -> None:
    p = default_export_path()
    assert p.parent.name == "exports"
    assert p.name.startswith("session-")
    assert p.suffix == ".md"


def test_export_command_registered() -> None:
    from llm_code.cli.commands import KNOWN_COMMANDS

    assert "export" in KNOWN_COMMANDS

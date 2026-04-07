"""Streaming markdown export of conversation sessions.

Renders a session's messages to a markdown file in chunks so large sessions
can be exported without building the full string in memory. The rendering
functions accept plain dicts with the shape used by conversation_db /
session.py:

    {"role": "user" | "assistant" | "tool" | "system",
     "content": str | list[dict],
     "tool_name": str (optional, for tool messages),
     "timestamp": str (optional)}
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable


_ROLE_HEADERS = {
    "user": "## User",
    "assistant": "## Assistant",
    "tool": "### Tool",
    "system": "### System",
}


def _stringify_content(content: object) -> str:
    """Flatten message content (str or list of blocks) into a markdown body."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
                elif btype == "tool_use":
                    name = block.get("name", "tool")
                    parts.append(f"```tool_use:{name}\n{block.get('input', {})}\n```")
                elif btype == "tool_result":
                    parts.append(
                        f"```tool_result\n{_stringify_content(block.get('content', ''))}\n```"
                    )
                elif btype == "image":
                    parts.append("*(image omitted)*")
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n\n".join(parts)
    return str(content)


def render_message_to_markdown(msg: dict) -> str:
    """Render a single message dict to a markdown fragment."""
    role = str(msg.get("role", "unknown")).lower()
    header = _ROLE_HEADERS.get(role, f"### {role.title()}")
    timestamp = msg.get("timestamp", "")
    ts_suffix = f" — {timestamp}" if timestamp else ""
    tool_name = msg.get("tool_name", "")
    if role == "tool" and tool_name:
        header = f"### Tool: `{tool_name}`"
    body = _stringify_content(msg.get("content", ""))
    return f"{header}{ts_suffix}\n\n{body}\n"


def export_session_streaming(
    messages: Iterable[dict],
    output_path: Path,
    chunk_size: int = 50,
    header: str | None = None,
) -> int:
    """Stream-render messages to a markdown file in chunks.

    Writes the header (if given), then iterates messages in groups of
    ``chunk_size`` flushing after each group. Returns the number of
    messages written.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    buffer: list[str] = []
    with output_path.open("w", encoding="utf-8") as f:
        if header:
            f.write(header.rstrip() + "\n\n")
        else:
            f.write(
                f"# Conversation Export\n\n_Exported {datetime.now().isoformat(timespec='seconds')}_\n\n"
            )
        for msg in messages:
            buffer.append(render_message_to_markdown(msg))
            buffer.append("\n")
            count += 1
            if count % chunk_size == 0:
                f.write("".join(buffer))
                f.flush()
                buffer.clear()
        if buffer:
            f.write("".join(buffer))
            f.flush()
    return count


def default_export_path() -> Path:
    """Return the default export target ``~/.llmcode/exports/session-<ts>.md``."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / ".llmcode" / "exports" / f"session-{ts}.md"

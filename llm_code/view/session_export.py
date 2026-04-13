"""Session → Markdown renderer used by ``/export``.

Relocated from ``tui/command_dispatcher.py`` as part of M11 cutover.
The function is pure (no widget deps, no runtime) and renders a
``Session`` to a stable Markdown document: user turns, assistant
text/thinking, tool use/result blocks. Image blocks become placeholders
so base64 payloads don't make the export unreadable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["render_session_markdown"]


def render_session_markdown(session: Any) -> str:
    """Render a ``Session`` to a human-readable Markdown document."""
    from llm_code.api.types import (
        ImageBlock,
        ServerToolResultBlock,
        ServerToolUseBlock,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    lines: list[str] = []
    title = session.name or f"Session {session.id}"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Session ID:** `{session.id}`")
    lines.append(f"- **Project:** `{session.project_path}`")
    lines.append(f"- **Created:** {session.created_at}")
    lines.append(f"- **Updated:** {session.updated_at}")
    lines.append(f"- **Messages:** {len(session.messages)}")
    lines.append(
        f"- **Exported at:** {datetime.now().isoformat(timespec='seconds')}"
    )
    if session.tags:
        lines.append(f"- **Tags:** {', '.join(session.tags)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, msg in enumerate(session.messages, start=1):
        heading = (
            "User" if msg.role == "user"
            else "Assistant" if msg.role == "assistant"
            else msg.role.title()
        )
        lines.append(f"## {idx}. {heading}")
        lines.append("")
        for block in msg.content:
            if isinstance(block, TextBlock):
                lines.append(block.text.rstrip())
                lines.append("")
            elif isinstance(block, ThinkingBlock):
                lines.append("<details><summary>💭 thinking</summary>")
                lines.append("")
                lines.append("```")
                lines.append(block.content.rstrip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")
            elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                import json as _json
                try:
                    pretty = _json.dumps(
                        block.input, ensure_ascii=False, indent=2,
                    )
                except (TypeError, ValueError):
                    pretty = repr(block.input)
                lines.append(
                    f"**🔧 tool call:** `{block.name}` (id=`{block.id}`)"
                )
                lines.append("")
                lines.append("```json")
                lines.append(pretty)
                lines.append("```")
                lines.append("")
            elif isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
                is_err = getattr(block, "is_error", False)
                marker = "❌ tool error" if is_err else "✅ tool result"
                lines.append(
                    f"**{marker}** (tool_use_id=`{block.tool_use_id}`)"
                )
                lines.append("")
                lines.append("```")
                lines.append(str(block.content).rstrip())
                lines.append("```")
                lines.append("")
            elif isinstance(block, ImageBlock):
                lines.append(f"*[image · {block.media_type}]*")
                lines.append("")
            else:
                lines.append(f"*[{type(block).__name__}]* `{block!r}`")
                lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

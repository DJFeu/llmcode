"""Dual-track tool call parsing: native API format and XML tag format."""
from __future__ import annotations

import dataclasses
import json
import re
import uuid

_XML_TOOL_CALL_RE = re.compile(
    r"<tool_call>(.*?)</tool_call>",
    re.DOTALL,
)


@dataclasses.dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    args: dict
    source: str  # "native" | "xml_tag"


def parse_tool_calls(
    response_text: str,
    native_tool_calls: list[dict] | None,
) -> list[ParsedToolCall]:
    """Parse tool calls from either native API format or XML tags in text.

    If native_tool_calls is a non-empty list, parse those (native track).
    Otherwise, fall back to scanning response_text for <tool_call>...</tool_call> tags.
    """
    if native_tool_calls:
        return _parse_native(native_tool_calls)
    return _parse_xml(response_text)


def _parse_native(native: list[dict]) -> list[ParsedToolCall]:
    result: list[ParsedToolCall] = []
    for call in native:
        call_id = call.get("id", str(uuid.uuid4()))
        name = call.get("name", "")
        args = call.get("input", {})
        if not name:
            continue
        result.append(ParsedToolCall(id=call_id, name=name, args=args, source="native"))
    return result


def _parse_xml(text: str) -> list[ParsedToolCall]:
    result: list[ParsedToolCall] = []
    for match in _XML_TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = data.get("tool")
        if not name:
            continue
        args = data.get("args", {})
        call_id = str(uuid.uuid4())
        result.append(ParsedToolCall(id=call_id, name=name, args=args, source="xml_tag"))
    return result

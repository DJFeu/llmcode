"""Dual-track tool call parsing: native API format and XML tag format.

Two XML formats are supported, tried in order:

1. **JSON-payload format** (the original llm-code XML protocol):
   ``<tool_call>{"tool": "NAME", "args": {...}}</tool_call>``

2. **Hermes function-calling format** (Qwen3, NousHermes, and most
   tool-fine-tuned local models served via vLLM without
   ``--enable-auto-tool-choice``):
   ``<tool_call>``
   ``  <function=NAME>``
   ``    <parameter=KEY>``
   ``    VALUE``
   ``    </parameter>``
   ``  </function>``
   ``</tool_call>``
"""
from __future__ import annotations

import dataclasses
import json
import re
import uuid

_XML_TOOL_CALL_RE = re.compile(
    r"<tool_call>(.*?)</tool_call>",
    re.DOTALL,
)
_HERMES_FUNCTION_RE = re.compile(
    r"<function=([^>\s]+)\s*>(.*?)</function>",
    re.DOTALL,
)
_HERMES_PARAMETER_RE = re.compile(
    r"<parameter=([^>\s]+)\s*>(.*?)</parameter>",
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
    Otherwise, fall back to scanning response_text for ``<tool_call>``
    tags. Both JSON-payload and Hermes-function formats are accepted; we
    try JSON first (cheaper) and fall back to Hermes if that fails.
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
    """Scan the response text for tool calls, accepting both JSON-payload
    and Hermes function-calling formats inside ``<tool_call>`` blocks."""
    result: list[ParsedToolCall] = []
    for match in _XML_TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        # Try JSON-payload format first (cheaper).
        parsed = _parse_json_payload(raw)
        if parsed is None:
            # Fall back to Hermes function-calling format.
            parsed = _parse_hermes_block(raw)
        if parsed is not None:
            result.append(parsed)
    return result


def _parse_json_payload(raw: str) -> ParsedToolCall | None:
    """Parse the original llm-code JSON payload format.

    ``{"tool": "NAME", "args": {...}}`` — args defaults to ``{}``.
    Returns None if the block isn't valid JSON or has no ``tool`` key.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("tool")
    if not name:
        return None
    args = data.get("args", {})
    if not isinstance(args, dict):
        args = {}
    return ParsedToolCall(
        id=str(uuid.uuid4()), name=name, args=args, source="xml_tag"
    )


def _parse_hermes_block(raw: str) -> ParsedToolCall | None:
    """Parse the Hermes function-calling format used by Qwen3, NousHermes,
    and most vLLM-served tool-fine-tuned local models.

    ``<function=NAME>`` opens, ``<parameter=KEY>VALUE</parameter>`` repeats,
    ``</function>`` closes. Parameter values are stripped of leading/trailing
    whitespace; internal whitespace (including newlines) is preserved so
    multi-line content (e.g. file bodies) round-trips correctly.

    Returns None if no ``<function=...>`` is found inside the block.
    """
    fn_match = _HERMES_FUNCTION_RE.search(raw)
    if not fn_match:
        return None
    name = fn_match.group(1).strip()
    if not name:
        return None
    body = fn_match.group(2)
    args: dict = {}
    for param_match in _HERMES_PARAMETER_RE.finditer(body):
        key = param_match.group(1).strip()
        value = param_match.group(2).strip()
        if key:
            args[key] = value
    return ParsedToolCall(
        id=str(uuid.uuid4()), name=name, args=args, source="xml_tag"
    )

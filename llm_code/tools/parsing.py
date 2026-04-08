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
# Template-truncated form. Some chat templates (notably vLLM-served Qwen3
# in tool-calling mode) inject "<tool_call>\n<function=" as the assistant
# prompt PREFIX. The model continues with "NAME>...params...</function>",
# so the streamed body of <tool_call> starts with the bare function name
# followed by ">" (or directly by "{" for JSON-args variants) instead of
# with "<function=NAME>". Match identifier characters only at the start
# (so a literal "<function>" with no name still fails this regex and
# the body falls through to "no parse"). The separator can be:
#   - ``>``     — classic truncated form (PR #15, PR #16)
#   - ``{``     — variant 4 emits ``web_search{"args": ...}`` with no
#     ``>`` between name and JSON (captured 2026-04-08 from Qwen3.5)
_HERMES_FUNCTION_TRUNCATED_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:>|(?=\{))(.*?)(?:</function>|\Z)",
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

    Three sub-formats are supported:

    1. **Full form** — ``<function=NAME>`` opens, ``<parameter=KEY>VALUE</parameter>``
       blocks repeat, ``</function>`` closes.
    2. **Template-truncated form** — vLLM-served Qwen3 chat template
       injects ``<tool_call>\\n<function=`` as the assistant prompt
       prefix, so the streamed body of ``<tool_call>`` starts directly
       with the bare function name followed by ``>`` (e.g.
       ``web_search>...``). Parameters are still ``<parameter=...>``
       blocks.
    3. **Truncated + JSON args form** — same template-truncation as #2,
       but the body after ``NAME>`` is a JSON object instead of
       ``<parameter=...>`` blocks. The JSON may be at the top level
       (e.g. ``{"command": "ls"}``) or nested under ``args`` /
       ``arguments`` (e.g. ``{"args": {"query": "...", "max_results": 3}}``).
       This is what local Qwen3 sometimes emits when the chat template
       primes the model with the ``<function=`` prefix but the model's
       fine-tune produces JSON rather than parameter tags.

    Parameter values are stripped of leading/trailing whitespace; internal
    whitespace (including newlines) is preserved so multi-line content
    (e.g. file bodies) round-trips correctly.

    Returns None if no form matches.
    """
    # Try full form first.
    fn_match = _HERMES_FUNCTION_RE.search(raw)
    if fn_match:
        name = fn_match.group(1).strip()
        body = fn_match.group(2)
    else:
        # Fall back to template-truncated form.
        trunc_match = _HERMES_FUNCTION_TRUNCATED_RE.match(raw)
        if not trunc_match:
            return None
        name = trunc_match.group(1).strip()
        body = trunc_match.group(2)
    if not name:
        return None
    args = _parse_hermes_args(body)
    return ParsedToolCall(
        id=str(uuid.uuid4()), name=name, args=args, source="xml_tag"
    )


def _parse_hermes_args(body: str) -> dict:
    """Extract args from a Hermes function body.

    Strategy (in order):
    1. ``<parameter=KEY>VALUE</parameter>`` blocks. If any present, use them.
    2. JSON object payload — if the body (after stripping any trailing
       ``</function>``) parses as a JSON dict, use it. If the dict has an
       ``args`` or ``arguments`` key whose value is also a dict, prefer
       the inner dict (so ``{"args": {"query": "x"}}`` → ``{"query": "x"}``).
    3. Otherwise return ``{}`` — caller still gets a valid ParsedToolCall
       with the function name, just no args.
    """
    # 1) parameter blocks
    args: dict = {}
    for param_match in _HERMES_PARAMETER_RE.finditer(body):
        key = param_match.group(1).strip()
        value = param_match.group(2).strip()
        if key:
            args[key] = value
    if args:
        return args

    # 2) JSON payload — strip trailing </function> close if present, then
    # find the first '{' and try to parse from there to the matching '}'.
    candidate = body.strip()
    if candidate.endswith("</function>"):
        candidate = candidate[: -len("</function>")].strip()
    if not candidate.startswith("{"):
        # Try to find the first '{' anywhere in the body — handles
        # leading whitespace/newlines that strip() didn't catch.
        brace_idx = candidate.find("{")
        if brace_idx == -1:
            return {}
        candidate = candidate[brace_idx:]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    # Prefer wrapped 'args' / 'arguments' dict if present
    for wrapper in ("args", "arguments"):
        inner = data.get(wrapper)
        if isinstance(inner, dict):
            return inner
    return data

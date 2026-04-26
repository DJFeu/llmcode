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

# Variant 7 — Harmony / GLM key-value pair format. Captured
# 2026-04-24 from glm-5.1 mid-session. The body wraps each arg in
# a ``<arg_key>NAME</arg_key><arg_value>VALUE</arg_value>`` pair:
#
#     <tool_call>
#     web_search
#     <arg_key>query</arg_key>
#     <arg_value>今日熱門新聞</arg_value>
#     <arg_key>max_results</arg_key>
#     <arg_value>5</arg_value>
#     </tool_call>
#
# Values are parsed as raw strings; if the string itself is a valid
# JSON scalar (number / bool / null / array / object) we decode it
# so ``max_results`` reaches the tool as an int rather than "5".
# Tool name is the first non-empty line of the body.
_HARMONY_ARG_PAIR_RE = re.compile(
    r"<arg_key>\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*</arg_key>\s*"
    r"<arg_value>(.*?)</arg_value>",
    re.DOTALL,
)

# Variant 6 — GLM-5.1 chat-template format. Captured 2026-04-24 from
# llama.cpp --jinja serving glm-5.1. The on-wire shape is:
#
#     <tool_call>web_search}{"query":"今日新聞","max_results":5}</arg_value>
#
# Note the closing tag is ``</arg_value>`` (NOT ``</tool_call>``) and
# the tool name is followed by a literal ``}`` before the JSON args
# object opens. Multiple tool calls are separated by the ``→``
# (U+2192) arrow character rather than being wrapped in separate
# ``<tool_call>`` blocks — the stream parser strips the arrow.
#
# Name capture is a conservative Python-identifier regex so only
# legitimate tool names match. The JSON body is parsed by
# ``_parse_json_payload`` downstream which enforces the dict shape.
_GLM_TOOL_CALL_VARIANT_RE = re.compile(
    r"<tool_call>\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}(\{.*?\})\s*</arg_value>",
    re.DOTALL,
)

# Variant 5 — bare name-as-tag form, emitted by Qwen3.5-122B on some
# vLLM chat template configurations where NEITHER ``<tool_call>`` NOR
# ``<function=NAME>`` appears in the stream. The on-wire shape is:
#
#     <web_search>{"query": "今日熱門新聞", "max_results": 3}</web_search>
#
# The closing tag may NOT match the opening tag — observed in the wild:
#
#     <web_search>{"query": "今日熱門新聞", "max_results": 3}</search>
#
# That is: the function name IS the XML tag itself, and the body is
# a JSON object of args. The leading ``<`` may also be missing in
# terminal renderings (and possibly in the actual stream when the
# chat template prefix-injected it as a prompt prefix), so the regex
# makes the opening ``<`` optional.
#
# Name capture is a conservative Python-identifier regex so this
# variant only triggers when the tag name looks like a real function
# name (no dashes, no dots, no numbers at the start). Combined with
# the "only try when no <tool_call> matches were found" guard in
# ``_parse_xml`` and the JSON-must-parse-as-dict check in
# ``_parse_bare_name_tag``, false positives on legitimate XML-ish
# content are effectively impossible.
# Closing tag may differ from opening tag — Qwen3.5 sometimes emits
# ``<web_search>JSON</search>`` (truncated closer). We capture the
# opening name and accept ANY ``</identifier>`` as closer.
_HERMES_BARE_NAME_TAG_RE = re.compile(
    r"<?([a-zA-Z_][a-zA-Z0-9_]*)>\s*(\{.*?\})\s*</[a-zA-Z_][a-zA-Z0-9_]*>",
    re.DOTALL,
)

# Tag names variant 5 must NEVER interpret as a tool call, even when
# the body is valid JSON. These are reserved by the protocol and
# already handled by earlier parser stages — matching them here
# would double-count or re-interpret a malformed wrapper as its own
# tool named "tool_call".
_VARIANT_5_RESERVED_NAMES: frozenset[str] = frozenset({
    "tool_call",
    "think",
    "function",
    "parameter",
})


# Variant 8 — v15 M5 — inline WebFetch / WebSearch JSON detection.
# Models trained on Claude Code transcripts occasionally emit:
#
#     WebFetch{"url": "https://example.com", "prompt": "..."}
#     WebSearch{"query": "..."}
#     web_fetch{"url": "..."}
#
# …as plain text inside an assistant message — no ``<tool_call>``
# wrapper, no XML tag, just a function name immediately followed by
# a JSON object. The 6 v13 variants don't catch this exact shape; M5
# adds it as the last-priority fallback.
#
# The regex captures the function name as a Python identifier
# (alphabetic + underscore + digits, but starting with letter/_) and
# the JSON object that immediately follows. Whitespace between name
# and ``{`` is tolerated. The JSON pattern handles 1 level of nested
# braces (sufficient for WebFetch / WebSearch which take flat-or-
# slightly-nested arg shapes); deeper nesting is rejected to keep
# the regex bounded against catastrophic backtrack.
#
# Gating: the leaf parser checks ``known_tool_names`` and rejects
# matches whose normalised name (PascalCase → snake_case) isn't a
# registered tool this turn. This prevents false-positive matches
# on code blocks that happen to contain ``WebFetch{…}`` literally.
_WEBFETCH_INLINE_RE = re.compile(
    r"\b(WebFetch|WebSearch|web_fetch|web_search)\s*"
    r"(\{(?:[^{}]|\{[^{}]*\})*\})",
    re.S,
)


def _normalise_webfetch_name(
    name: str, known_tool_names: set[str] | frozenset[str] | None,
) -> str | None:
    """Map ``WebFetch`` / ``WebSearch`` to their snake_case registered
    names, falling back to the original if the snake_case form is not
    in the registry.

    Returns ``None`` when no variant is in ``known_tool_names`` (so
    the caller can skip the match). When ``known_tool_names`` is
    ``None`` (permissive mode used by some unit tests) the normalised
    snake_case name is returned without verification.
    """
    snake_map = {
        "WebFetch": "web_fetch",
        "WebSearch": "web_search",
        "web_fetch": "web_fetch",
        "web_search": "web_search",
    }
    snake = snake_map.get(name, name)
    if known_tool_names is None:
        return snake
    if snake in known_tool_names:
        return snake
    # Edge case: if the user registered the literal PascalCase name,
    # honour that rather than rewriting it.
    if name in known_tool_names:
        return name
    return None


def _parse_webfetch_inline(
    block_text: str,
    known_tool_names: set[str] | frozenset[str] | None = None,
) -> list[ParsedToolCall]:
    """Variant 8 — match inline WebFetch / WebSearch JSON in plain text.

    Gates on ``known_tool_names``: only fires when the matched
    function name (after PascalCase → snake_case normalisation) is in
    the registry. This is the production guard against false
    positives — without it, a literal ``WebFetch{…}`` inside a code
    block could be picked up and dispatched as a real tool call.
    """
    if known_tool_names is None:
        # Permissive mode (tests). The normaliser returns the
        # snake_case name without verification.
        pass
    elif not known_tool_names:
        # Empty registry → no matches possible. Bail fast.
        return []

    results: list[ParsedToolCall] = []
    for match in _WEBFETCH_INLINE_RE.finditer(block_text):
        raw_name = match.group(1)
        normalised = _normalise_webfetch_name(raw_name, known_tool_names)
        if normalised is None:
            continue
        try:
            args = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue
        results.append(ParsedToolCall(
            id=str(uuid.uuid4()),
            name=normalised,
            args=args,
            source="xml_tag",
        ))
    return results


@dataclasses.dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    args: dict
    source: str  # "native" | "xml_tag"


def parse_tool_calls(
    response_text: str,
    native_tool_calls: list[dict] | None,
    known_tool_names: set[str] | frozenset[str] | None = None,
    *,
    profile: "object | None" = None,
) -> list[ParsedToolCall]:
    """Parse tool calls from either native API format or XML tags in text.

    If native_tool_calls is a non-empty list, parse those (native track).
    Otherwise, fall back to scanning response_text for ``<tool_call>``
    tags. Both JSON-payload and Hermes-function formats are accepted; we
    try JSON first (cheaper) and fall back to Hermes if that fails.

    ``known_tool_names`` restricts the bare ``<NAME>JSON</NAME>`` variant
    5 fallback to tags whose name matches a registered tool. Callers
    without a tool registry (e.g. tests) can pass ``None`` and the
    variant runs unrestricted — keep in mind that permissive mode can
    match ``<p>{"a":1}</p>`` and similar HTML-ish content as a false
    positive, so production callers should always pass the set.

    ``profile`` — v13: ``ModelProfile`` (or None). When a profile
    with a non-empty ``parser_variants`` tuple is provided, the
    variant order is read from it via
    ``tools.parser_variants.REGISTRY``. When ``None`` or the tuple is
    empty, ``DEFAULT_VARIANT_ORDER`` is used so profile-less callers
    (notably the stream parser's internal recovery path) keep working.
    """
    if native_tool_calls:
        return _parse_native(native_tool_calls)
    return _parse_xml(response_text, known_tool_names, profile=profile)


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


def _parse_xml(
    text: str,
    known_tool_names: set[str] | frozenset[str] | None = None,
    *,
    profile: "object | None" = None,
) -> list[ParsedToolCall]:
    """Scan the response text for tool calls, accepting the JSON-payload
    and Hermes function-calling formats inside ``<tool_call>`` blocks,
    plus the bare ``<NAME>JSON</NAME>`` variant from Qwen3.5 vLLM chat
    templates that omit the ``<tool_call>`` wrapping entirely.

    Variant order is profile-driven (v13): when
    ``profile.parser_variants`` is non-empty, that order wins.
    Otherwise ``DEFAULT_VARIANT_ORDER`` (from ``parser_variants``) is
    used — this keeps profile-less callers (tests, StreamParser
    internal recovery) working with the historical variant sequence.
    """
    # Local import to avoid circular import — parser_variants imports
    # parsing for the regex constants + leaf parse functions.
    from llm_code.tools.parser_variants import (
        DEFAULT_VARIANT_ORDER,
        _WRAPPER_LESS_SCANNERS,
        get_variant,
    )

    # Pull ordered variants from profile, falling back to the default
    # historical order. Empty tuple / missing attr / profile=None all
    # resolve to DEFAULT_VARIANT_ORDER.
    raw_order = getattr(profile, "parser_variants", None) if profile else None
    order: tuple[str, ...] = (
        tuple(raw_order) if raw_order else DEFAULT_VARIANT_ORDER
    )

    # Per-block loop: for each ``<tool_call>…</tool_call>`` match, try
    # variants in order. First variant whose ``match`` predicate fires
    # AND whose ``parse`` returns non-None wins.
    result: list[ParsedToolCall] = []
    for match in _XML_TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        for variant_name in order:
            variant = get_variant(variant_name)
            if not variant.match(raw):
                continue
            parsed = variant.parse(raw)
            if parsed is not None:
                result.append(parsed)
                break

    # Wrapper-less fallbacks. Only fire when the per-block loop found
    # nothing, preserving the existing fast path for well-formed
    # emissions. The scanners walk the full text so they handle the
    # case where the ``<tool_call>`` wrapper was never emitted.
    if not result:
        for variant_name in order:
            scanner = _WRAPPER_LESS_SCANNERS.get(variant_name)
            if scanner is None:
                continue
            # v15 M5 — webfetch_inline takes the same ``(text,
            # known_tool_names)`` shape as bare_name_tag for the same
            # reason: the registry guard MUST run at the wrapper-less
            # scanner level to suppress false-positive matches on
            # code blocks containing literal ``WebFetch{…}`` text.
            if variant_name in ("bare_name_tag", "webfetch_inline"):
                extras = scanner(text, known_tool_names)  # type: ignore[arg-type]
            else:
                extras = scanner(text)  # type: ignore[call-arg]
            if extras:
                result.extend(extras)
                break
    return result


def _parse_harmony_variant(raw: str) -> ParsedToolCall | None:
    """Variant 7 — Harmony/GLM ``<arg_key>/<arg_value>`` key-value body.

    The caller passes the raw body (content between ``<tool_call>``
    and ``</tool_call>``, with the wrapper tags already stripped by
    ``_XML_TOOL_CALL_RE``). The first non-empty line is the tool
    name; subsequent ``<arg_key>K</arg_key><arg_value>V</arg_value>``
    pairs populate the args dict. Values that round-trip as JSON
    scalars / arrays / objects are decoded so ``{"max_results": 5}``
    instead of ``{"max_results": "5"}`` reaches the runtime.
    """
    pairs = _HARMONY_ARG_PAIR_RE.findall(raw)
    if not pairs:
        return None
    # Extract the tool name from the region BEFORE the first pair.
    first_pair_at = _HARMONY_ARG_PAIR_RE.search(raw)
    preamble = raw[: first_pair_at.start()] if first_pair_at else raw
    name = ""
    for line in preamble.splitlines():
        candidate = line.strip()
        if candidate:
            name = candidate
            break
    if not name or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
        return None
    if name in _VARIANT_5_RESERVED_NAMES:
        return None
    args: dict = {}
    for key, value in pairs:
        stripped = value.strip()
        try:
            decoded = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            decoded = stripped
        args[key] = decoded
    return ParsedToolCall(
        id=str(uuid.uuid4()),
        name=name,
        args=args,
        source="xml_tag",
    )


def _parse_glm_variant(text: str) -> list[ParsedToolCall]:
    """Variant 6 — GLM-5.1 ``<tool_call>NAME}{JSON}</arg_value>``.

    Supports multiple tool calls in a single emission (GLM's chat
    template separates them with the ``→`` U+2192 arrow character).
    Each match is parsed independently; a bad JSON body or non-dict
    shape skips that match without aborting the others.
    """
    result: list[ParsedToolCall] = []
    for m in _GLM_TOOL_CALL_VARIANT_RE.finditer(text):
        name = m.group(1).strip()
        if not name or name in _VARIANT_5_RESERVED_NAMES:
            continue
        try:
            args = json.loads(m.group(2))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        result.append(
            ParsedToolCall(
                id=str(uuid.uuid4()),
                name=name,
                args=args,
                source="xml_tag",
            )
        )
    return result


def _parse_bare_name_tag(
    text: str,
    known_tool_names: set[str] | frozenset[str] | None = None,
) -> list[ParsedToolCall]:
    """Variant 5 — parse bare ``<NAME>JSON</NAME>`` tool calls.

    Only called from ``_parse_xml`` when no ``<tool_call>`` wrapper
    match was found, so the fast path for well-formed emissions is
    untouched. Each match must contain a JSON object body; scalars,
    lists, and invalid JSON are rejected as false-positive guards.

    When ``known_tool_names`` is provided, tag names not in the set
    are rejected — this is the production guard against false
    positives like ``<p>{"a":1}</p>`` that the identifier-only regex
    would otherwise capture. Callers without a registry (e.g. tests)
    can pass ``None`` for permissive matching.

    Also handles common arg-nesting shapes the same way the existing
    Hermes truncated-JSON variant does:

    1. ``{"key": "value", ...}``          — args at top level
    2. ``{"args": {"key": "value"}}``     — args nested under "args"
    3. ``{"arguments": {"key": "value"}}`` — args nested under "arguments"
    """
    result: list[ParsedToolCall] = []
    for match in _HERMES_BARE_NAME_TAG_RE.finditer(text):
        name = match.group(1)
        if name in _VARIANT_5_RESERVED_NAMES:
            continue
        if known_tool_names is not None and name not in known_tool_names:
            continue
        body = match.group(2).strip()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # Unwrap common nesting shapes. If the body is already flat
        # args, pass it through.
        if isinstance(data.get("args"), dict):
            args = data["args"]
        elif isinstance(data.get("arguments"), dict):
            args = data["arguments"]
        else:
            args = data
        result.append(
            ParsedToolCall(
                id=str(uuid.uuid4()),
                name=name,
                args=args,
                source="xml_tag",
            )
        )
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

    # 2) JSON payload — strip trailing XML closing tag if present, then
    # find the first '{' and try to parse from there to the matching '}'.
    # The closing tag may be </function>, </search>, </web_search>, etc.
    candidate = body.strip()
    _trail_tag = re.search(r"</[a-zA-Z_][a-zA-Z0-9_]*>\s*$", candidate)
    if _trail_tag:
        candidate = candidate[: _trail_tag.start()].strip()
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

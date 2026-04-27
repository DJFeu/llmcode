"""Cross-provider message-shape conversion (v15 M3).

Single source of truth for ``Message`` → wire ``dict[]`` conversion.
Replaces logic previously duplicated across ``anthropic_provider.py``
and ``openai_compat.py``.

Three concerns are separated:

* **Message shape** — :func:`serialize_messages` walks
  ``tuple[Message, ...]`` and emits the wire ``list[dict[str, Any]]``
  in the requested target shape (``"anthropic"`` or ``"openai"``).
* **Tool-result payload** — :func:`serialize_tool_result` provides
  stable JSON encoding of any tool_result content (None, str, dict,
  list) so both providers agree on the wire shape.
* **Reasoning replay** — :class:`ReasoningReplayMode` enum +
  application logic for how prior-turn reasoning is replayed
  (``DISABLED`` / ``THINK_TAGS`` / ``REASONING_CONTENT`` /
  ``NATIVE_THINKING``).

:class:`ConversionContext` packages the per-call options. Providers
build their own ``ctx`` instance from their resolved ``ModelProfile``;
this module never reads profiles directly so it stays pure /
unit-testable.

**Byte-parity gate.** A 49-scenario corpus
(``tests/fixtures/conversion_corpus.json``) was captured from the
v2.4.0 codebase before this module existed. The parity test
``tests/test_api/parity/test_provider_conversion_parity_v15.py``
asserts byte-identical output across every scenario; any drift is a
regression.

Architecture borrowed from
``Alishahryar1/free-claude-code/core/anthropic/conversion.py``. We
keep the structural decomposition (separate functions for tool-result
serialization, deferred-block reordering, reasoning replay) but
re-implement against our :class:`Message` / :class:`ContentBlock`
shape (frozen dataclasses, ``tuple[ContentBlock, ...]``).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from llm_code.api.types import (
    ContentBlock,
    ImageBlock,
    Message,
    ServerToolResultBlock,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

_logger = logging.getLogger(__name__)
# OpenAI-compat-specific log lines keep their original logger name
# (``llm_code.api.openai_compat``) so existing tests + log scrapers
# that filter by that name still see them after the M3 refactor.
_openai_logger = logging.getLogger("llm_code.api.openai_compat")


# ── Reasoning replay strategy ────────────────────────────────────────


class ReasoningReplayMode(Enum):
    """How prior-turn reasoning content is replayed on the next request.

    * ``DISABLED`` — drop all reasoning blocks. The model generates
      fresh reasoning next turn (cheapest; loses continuity).
    * ``THINK_TAGS`` — wrap prior thinking in ``<think>...</think>``
      tags inside the assistant content (works for any provider that
      tolerates think tags but doesn't have a native channel).
    * ``REASONING_CONTENT`` — surface prior thinking as the
      ``reasoning_content`` key on the assistant message
      (DeepSeek-R1 / Qwen QwQ / OpenAI o-series).
    * ``NATIVE_THINKING`` — Anthropic's signed ``thinking`` content
      block; round-trips signature verbatim.

    The OpenAI-compat provider in v2.4.0 dropped thinking blocks
    entirely on the way out (the server rejected unknown content
    types). M3 preserves that behaviour by treating that path as
    ``DISABLED`` regardless of the profile's preferred mode.
    """
    DISABLED = "disabled"
    THINK_TAGS = "think_tags"
    REASONING_CONTENT = "reasoning_content"
    NATIVE_THINKING = "native_thinking"


@dataclass(frozen=True)
class ConversionContext:
    """Per-call options consumed by :func:`serialize_messages`.

    Providers build the context from their resolved
    :class:`~llm_code.runtime.model_profile.ModelProfile` and the
    target wire shape. The dataclass is frozen so a single
    ``ConversionContext`` can be re-used safely across requests.
    """
    target_shape: Literal["anthropic", "openai"]
    reasoning_replay: ReasoningReplayMode = ReasoningReplayMode.DISABLED
    strip_prior_reasoning: bool = False  # v14 mech B carry-through


# ── Tool-result serialization ────────────────────────────────────────


def serialize_tool_result(content: object) -> str:
    """Stable JSON serialization of any tool_result payload.

    Maps ``None`` → ``""``, ``str`` → str, ``dict`` / ``list`` →
    ``json.dumps`` with ``ensure_ascii=False`` (so unicode round-trips
    cleanly). Everything else falls back to ``str(...)``.

    Used by both providers when the wire shape needs a string body
    for a ``tool_result``. Anthropic accepts any content; OpenAI
    compat strictly requires a string. Centralising the behaviour
    keeps both call sites aligned.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


# ── Deferred block reordering ────────────────────────────────────────


def deferred_post_tool_blocks(
    blocks: tuple[ContentBlock, ...],
) -> tuple[ContentBlock, ...]:
    """Return blocks that must be deferred until after tool_result
    replay (OpenAI-compat constraint).

    OpenAI ``chat.completions`` cannot place assistant text *after*
    ``tool_calls`` in the same message, so any non-tool_use block
    appearing after the first tool_use needs to be emitted as a
    separate assistant message *after* the corresponding tool results.

    Anthropic accepts the original order verbatim — this function
    is a no-op there.
    """
    first_tool_idx: int | None = None
    for i, block in enumerate(blocks):
        if isinstance(block, ToolUseBlock):
            first_tool_idx = i
            break
    if first_tool_idx is None:
        return ()
    deferred: list[ContentBlock] = []
    for i in range(first_tool_idx + 1, len(blocks)):
        if not isinstance(blocks[i], ToolUseBlock):
            deferred.append(blocks[i])
    return tuple(deferred)


# ── Anthropic shape ──────────────────────────────────────────────────


def _anthropic_block_to_dict(block: ContentBlock) -> dict[str, Any]:
    """Encode one content block as Anthropic's wire dict."""
    if isinstance(block, ThinkingBlock):
        entry: dict[str, Any] = {
            "type": "thinking",
            "thinking": block.content,
        }
        if block.signature:
            entry["signature"] = block.signature
        return entry
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }
    if isinstance(block, ServerToolUseBlock):
        entry = {
            "type": "server_tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
        if block.signature:
            entry["signature"] = block.signature
        return entry
    if isinstance(block, ServerToolResultBlock):
        entry = {
            "type": "server_tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
        }
        if block.signature:
            entry["signature"] = block.signature
        return entry
    raise ValueError(f"unhandled block: {type(block).__name__}")


def _anthropic_convert_message(msg: Message) -> dict[str, Any]:
    """Convert a Message to Anthropic wire shape.

    Tool result messages get a flat content array of ``tool_result``
    entries; mixed content is encoded block-by-block.
    """
    # Tool result messages — single or multi.
    if msg.content and all(isinstance(b, ToolResultBlock) for b in msg.content):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    **({"is_error": True} if b.is_error else {}),
                }
                for b in msg.content
                if isinstance(b, ToolResultBlock)
            ],
        }

    content: list[dict[str, Any]] = []
    for block in msg.content:
        # NB: ToolResultBlock inside mixed content (i.e. a user
        # message that contains both TextBlock and ToolResultBlock) is
        # silently dropped here — preserving v2.4.0 behaviour. The
        # canonical shape is "tool_result-only message"; mixed messages
        # are rare and the v2.4.0 path discarded the result. Keeping
        # parity prevents a behaviour change from sneaking through the
        # M3 refactor; correctness fixes are a separate concern.
        if isinstance(block, ToolResultBlock):
            continue
        content.append(_anthropic_block_to_dict(block))
    return {"role": msg.role, "content": content}


def _serialize_anthropic(
    messages: tuple[Message, ...],
) -> list[dict[str, Any]]:
    """Anthropic wire-shape converter — single source of truth.

    Mirrors the v2.4.0 ``anthropic_provider._build_messages``
    behaviour byte-for-byte, including the prompt-caching
    ``cache_control`` breakpoint placed on the last user message's
    final content block.
    """
    result: list[dict[str, Any]] = [
        _anthropic_convert_message(msg) for msg in messages
    ]

    # Prompt caching: add cache_control breakpoint on the last content
    # block of the last user message. The payload-builder may append
    # additional cache_control on system / tool blocks; the per-message
    # breakpoint here is the canonical "cache up to this turn" mark.
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            content = result[i].get("content")
            if isinstance(content, list) and content:
                content[-1]["cache_control"] = {"type": "ephemeral"}
            break

    return result


# ── OpenAI compat shape ──────────────────────────────────────────────


def _openai_convert_message(msg: Message) -> dict[str, Any]:
    """Convert one Message to OpenAI-compat wire shape.

    Mirrors v2.4.0 ``openai_compat._convert_message``: tool-result
    messages collapse to ``role: tool``; mixed-content messages emit
    a parts array (text/image_url); single text → string content;
    fallback concatenates text blocks. Thinking blocks are dropped on
    the way out (OpenAI-compat servers reject unknown content types).
    """
    # Tool result messages → role: tool with the first block's payload.
    if msg.role == "tool" or (
        len(msg.content) == 1 and isinstance(msg.content[0], ToolResultBlock)
    ):
        block = msg.content[0]
        assert isinstance(block, ToolResultBlock)
        return {
            "role": "tool",
            "tool_call_id": block.tool_use_id,
            "content": block.content,
        }

    # v2.5.2 — Assistant messages carrying ToolUseBlocks must serialize
    # to OpenAI's ``tool_calls`` array, not the parts-array branch
    # (which has no place for ToolUseBlocks and drops them silently).
    # Without this, a native-tool-calling model's prior turn loses its
    # caller IDs on the next request, breaking the assistant↔tool
    # pairing that role:tool messages depend on. Codex stop-time
    # review caught this gap in v2.5.1; the user-side
    # _split_bundled_tool_results fix surfaced it by making the
    # downstream role:tool messages no longer get silently dropped.
    if msg.role == "assistant" and any(
        isinstance(b, ToolUseBlock) for b in msg.content
    ):
        tool_calls_arr: list[dict[str, Any]] = []
        text_parts: list[str] = []
        thinking_dropped = 0
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_calls_arr.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input or {}),
                    },
                })
            elif isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ThinkingBlock):
                thinking_dropped += 1
        if thinking_dropped:
            _warn_thinking_dropped_once(thinking_dropped)
        out: dict[str, Any] = {"role": "assistant"}
        # OpenAI spec: content may be string or null when tool_calls
        # are present. Use null only when no text was emitted, so
        # downstream renderers don't see an empty string.
        out["content"] = "".join(text_parts) if text_parts else None
        out["tool_calls"] = tool_calls_arr
        return out

    has_image = any(isinstance(b, ImageBlock) for b in msg.content)
    has_multiple = len(msg.content) > 1

    if has_image or has_multiple:
        parts: list[dict[str, Any]] = []
        thinking_dropped = 0
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append({"type": "text", "text": block.text})
            elif isinstance(block, ImageBlock):
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block.media_type};base64,{block.data}"
                    },
                })
            elif isinstance(block, ThinkingBlock):
                # OpenAI-compat servers reject thinking blocks; drop
                # silently. The model generates fresh reasoning next
                # turn.
                thinking_dropped += 1
        if thinking_dropped:
            _warn_thinking_dropped_once(thinking_dropped)
        return {"role": msg.role, "content": parts}

    # Single text block — string content for simplicity.
    if len(msg.content) == 1 and isinstance(msg.content[0], TextBlock):
        return {"role": msg.role, "content": msg.content[0].text}

    # Fallback: concatenate text blocks (drops thinking implicitly).
    text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    return {"role": msg.role, "content": text}


def _warn_thinking_dropped_once(count: int) -> None:
    """One-shot debug log when thinking blocks are dropped on the
    OpenAI-compat outbound path.

    The "warned" flag lives on the legacy ``llm_code.api.openai_compat``
    module so existing test fixtures that reset
    ``openai_compat._thinking_drop_warned`` between tests still work
    after the M3 refactor. The fixture preserves the contract that
    each test starts with a clean slate.
    """
    # Lazy import to avoid a circular dependency — openai_compat
    # imports conversion at module load.
    from llm_code.api import openai_compat as _oc
    if _oc._thinking_drop_warned:
        return
    _oc._thinking_drop_warned = True
    _openai_logger.debug(
        "openai_compat: dropping %d thinking block(s) from outbound "
        "assistant message — OpenAI-compat servers reject unknown "
        "content types.",
        count,
    )


def _strip_reasoning_keys(out: dict[str, Any]) -> int:
    """v14 Mechanism B — drop reasoning channel keys from an outbound
    message dict, returning the byte count of what was removed.

    Today the conversion path drops ThinkingBlocks before emitting the
    dict (OpenAI-compat servers reject unknown content types), so
    reasoning never reaches the outbound dict in stock code. This
    filter is defensive: any subclass / future change that lands a
    raw ``reasoning_content`` or ``reasoning`` string on an outbound
    message will have it stripped here when the active context opts
    in via ``ctx.strip_prior_reasoning=True``.
    """
    removed_bytes = 0
    for key in ("reasoning_content", "reasoning"):
        value = out.pop(key, None)
        if isinstance(value, str):
            removed_bytes += len(value)
        elif value is not None:
            # Anything non-string (list, dict) — count its repr bytes
            # to keep the metric meaningful.
            removed_bytes += len(repr(value))
    return removed_bytes


def _split_bundled_tool_results(
    messages: tuple[Message, ...],
) -> tuple[Message, ...]:
    """v2.5.1 fix — explode multi-ToolResultBlock user messages into
    one ``role: "tool"``-shaped message per block.

    ``conversation.py:2069`` bundles every ToolResultBlock from a
    single turn into one ``Message(role="user", content=tuple(...))``.
    OpenAI-compat servers expect one ``role: tool`` message per
    ``tool_call_id`` though, and ``_openai_convert_message`` only maps
    bundled (len > 1) inputs to a parts-array which has no place for
    ToolResultBlocks — they would be silently dropped, leaving the
    model without the result it just produced. The v14 mech-A
    post-tool reminder then lies ("You just called web_search and
    received the result above") and the model — correctly — rejects
    it as an injection. Observed against GLM-5.1 in v2.5.0; root
    cause traces to v14 + v15 stacked on the bundled-message shape.

    The split converts ``Message(role=..., content=(R1, R2))`` into
    ``Message(role=..., content=(R1,)), Message(role=..., content=(R2,))``
    so each downstream message hits the existing single-block path
    that maps to ``role: tool`` correctly. Mixed content (a
    ToolResultBlock alongside a TextBlock in one user message) is
    left untouched — that path was never used by the runtime and
    keeping it stable preserves the M3 byte-parity gate.
    """
    expanded: list[Message] = []
    for msg in messages:
        if len(msg.content) > 1 and all(
            isinstance(b, ToolResultBlock) for b in msg.content
        ):
            for block in msg.content:
                expanded.append(Message(role=msg.role, content=(block,)))
        else:
            expanded.append(msg)
    return tuple(expanded)


# ── v2.9.0 P2 — tool-result compression on re-feed ───────────────────


# Hard cap on the per-result preview length retained in the truncated
# marker. 500 chars is enough to keep title/url + a 2-3 sentence
# excerpt for every common tool (web_search, web_fetch, research),
# which is what downstream re-feeds need to cite. Anything longer is
# already in session history if the model wants the full payload.
_COMPRESS_PREVIEW_CHARS: int = 500

# Marker placed in front of every compressed payload so users (and
# tests) can grep for the v2.9 lever in the wire dump. Includes the
# version tag for forward-compat traceability.
_COMPRESS_MARKER_PREFIX: str = "[v2.9 compressed]"


def _looks_like_tool_result_message(msg: Message) -> bool:
    """True when ``msg`` is a tool-result-only user/tool message.

    Both providers route these to ``role: tool`` (OpenAI) or
    ``tool_result`` (Anthropic) and the conversion picks a stable
    block path; compression replaces only the content payload, the
    block structure stays intact.
    """
    return bool(msg.content) and all(
        isinstance(b, ToolResultBlock) for b in msg.content
    )


def _truncate_tool_result_payload(content: object) -> str:
    """Return a structured truncated marker for a tool_result body.

    ``content`` may be ``None``, a ``str``, a ``list`` of dicts (the
    Anthropic block array shape), or a ``dict`` (cohere-style
    sourced answer). All variants are normalised through
    :func:`serialize_tool_result` so the compression sees the same
    string the model would otherwise have read on the wire.

    The returned marker is plain text so both providers accept it:
    OpenAI-compat puts it in the ``content`` string verbatim;
    Anthropic wraps it in a ``{"type": "text", "text": ...}`` block
    via :func:`_anthropic_block_to_dict`.

    Idempotence — bodies that already begin with the v2.9 marker
    prefix are returned verbatim. The compression step is wired into
    every outbound serialization, so a profile that compresses on
    iteration 1 must not double-truncate on iteration 2.
    """
    body = serialize_tool_result(content)
    if body.startswith(_COMPRESS_MARKER_PREFIX):
        # Already compressed — leave it alone (idempotence).
        return body
    preview = body[:_COMPRESS_PREVIEW_CHARS]
    truncated_chars = max(len(body) - _COMPRESS_PREVIEW_CHARS, 0)
    if truncated_chars <= 0:
        # Nothing to compress — keep the original body so the wire
        # payload is identical to the un-compressed path.
        return body
    return (
        f"{_COMPRESS_MARKER_PREFIX} preview ({_COMPRESS_PREVIEW_CHARS} chars "
        f"of {len(body)}):\n"
        f"{preview}\n"
        f"[full content omitted to reduce prefill cost — {truncated_chars} "
        f"chars hidden. The most recent tool result for this turn was kept "
        f"intact; refer to it for the complete payload.]"
    )


def _compressed_block(block: ToolResultBlock) -> ToolResultBlock:
    """Return a copy of ``block`` with its content replaced by the
    truncated marker. ``is_error`` and ``tool_use_id`` are preserved
    so the provider still pairs the compressed result with the right
    ``tool_call_id``.
    """
    return ToolResultBlock(
        tool_use_id=block.tool_use_id,
        content=_truncate_tool_result_payload(block.content),
        is_error=block.is_error,
    )


def compress_old_tool_results(
    messages: tuple[Message, ...],
) -> tuple[Message, ...]:
    """v2.9.0 P2 — replace older tool_result payloads with truncated
    markers, leaving the most recent contiguous batch intact.

    "Most recent batch" is the trailing run of tool-result-only
    messages (after the bundle splitter, every such message holds a
    single ToolResultBlock; before the splitter, a bundle holds N).
    Anything before the trailing batch (i.e. tool results from a
    prior iteration) gets compressed; the trailing batch stays as-is
    because the model is currently reasoning over it and needs the
    full payload.

    Non-tool-result messages (assistant text, user prompts, mixed
    content) are passed through verbatim. Compression is purely
    additive: ``ToolResultBlock`` blocks get a smaller content
    string; the message tuple shape, ordering, and per-block
    metadata (tool_use_id, is_error) are unchanged.

    The transform is idempotent — already-compressed bodies start
    with ``[v2.9 compressed]`` and the truncate path detects the
    short payload and returns it verbatim.
    """
    if not messages:
        return messages

    # Walk from the tail backwards to find the contiguous trailing
    # tool-result batch. ``preserve_from`` is the index of the first
    # message in that batch — everything at or after it stays full.
    preserve_from = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if _looks_like_tool_result_message(messages[i]):
            preserve_from = i
        else:
            break
    if preserve_from == 0:
        # The whole conversation is tool results (unusual but valid).
        # Treat the entire trailing run as "most recent" — nothing to
        # compress because there's no older batch.
        return messages

    out: list[Message] = []
    for idx, msg in enumerate(messages):
        if idx >= preserve_from:
            out.append(msg)
            continue
        if not _looks_like_tool_result_message(msg):
            out.append(msg)
            continue
        compressed = tuple(
            _compressed_block(b) if isinstance(b, ToolResultBlock) else b
            for b in msg.content
        )
        out.append(Message(role=msg.role, content=compressed))
    return tuple(out)


def _serialize_openai(
    messages: tuple[Message, ...],
    *,
    system: str | None = None,
    strip_prior_reasoning: bool = False,
) -> list[dict[str, Any]]:
    """OpenAI-compat wire-shape converter — single source of truth.

    Mirrors v2.4.0 ``openai_compat._build_messages`` byte-for-byte,
    including the optional ``system`` prepend and the v14 mech B
    reasoning-content history filter.

    v2.5.1: pre-pass via :func:`_split_bundled_tool_results` to
    expand bundled ToolResultBlock user messages into one message per
    block before per-message conversion. Required for OpenAI-compat
    servers that map each result to a separate ``role: tool`` entry.
    """
    result: list[dict[str, Any]] = []

    if system:
        result.append({"role": "system", "content": system})

    reasoning_strip_count = 0
    reasoning_strip_bytes = 0

    messages = _split_bundled_tool_results(messages)
    for msg in messages:
        converted = _openai_convert_message(msg)
        if (
            strip_prior_reasoning
            and converted.get("role") == "assistant"
        ):
            removed = _strip_reasoning_keys(converted)
            if removed:
                reasoning_strip_count += 1
                reasoning_strip_bytes += removed
        result.append(converted)

    if reasoning_strip_count:
        _openai_logger.info(
            "tool_consumption: reasoning_stripped turns=%d total_bytes=%d",
            reasoning_strip_count, reasoning_strip_bytes,
        )

    return result


# ── Public entry point ───────────────────────────────────────────────


def serialize_messages(
    messages: tuple[Message, ...], ctx: ConversionContext,
    *,
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Serialize a conversation to wire ``list[dict[str, Any]]``.

    Dispatches by ``ctx.target_shape``. Both branches agree on tool-
    result serialization (:func:`serialize_tool_result`); they diverge
    on assistant-message structure (Anthropic block list vs OpenAI
    parts/string).

    The OpenAI branch consumes ``system`` (the request's top-level
    system prompt) and prepends a ``role: system`` message. The
    Anthropic branch ignores ``system`` here — that provider attaches
    the system prompt outside the messages array (see
    ``anthropic_provider._build_payload``).
    """
    if ctx.target_shape == "anthropic":
        return _serialize_anthropic(messages)
    if ctx.target_shape == "openai":
        return _serialize_openai(
            messages,
            system=system,
            strip_prior_reasoning=ctx.strip_prior_reasoning,
        )
    raise ValueError(f"unknown target_shape: {ctx.target_shape!r}")

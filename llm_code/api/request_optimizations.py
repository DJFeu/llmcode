"""Trivial-request interception (v15 M1).

Detects 5 patterns where an LLM call has a deterministic answer and
short-circuits with a synthetic response. Saves quota + latency on
Claude-Code-style agent traffic where these patterns recur.

The 5 detectors:

* ``_quota_mock`` — ``max_tokens=1`` AND a single user message
  containing the word "quota". Return a 1-token mock response.
* ``_prefix_detection`` — message contains ``<policy_spec>`` and a
  ``Command:`` line. Extract the shell command's prefix locally and
  return that as the body.
* ``_title_skip`` — system prompt asks for a "sentence-case title"
  AND a JSON return shape. Return ``"Conversation"`` (matches Claude
  Code's title-fallback string).
* ``_suggestion_skip`` — user message starts with ``[SUGGESTION
  MODE:``. Return empty content (no suggestion).
* ``_filepath_mock`` — single user message contains ``Command:`` +
  ``Output:`` + the word "filepaths" (or a ``<filepaths>`` tag).
  Parse the command + output locally, extract paths via
  ``shlex``-aware regex, return the result.

Each detector is a pure function: ``MessageRequest -> MessageResponse |
None``. ``None`` means "not my pattern; let the next detector / real
call run". ``try_optimize`` walks the registry in declaration order
and returns the first hit.

Profile-gated via ``profile.enable_request_optimizations: bool = True``.
Profile that wants every call to hit the model (e.g. testing) sets to
False.

Architecture borrowed from ``Alishahryar1/free-claude-code`` (proxy
fast-path); the detector predicates were rewritten for our
``MessageRequest`` shape (frozen dataclass, ``tuple[Message, ...]``)
and the synthetic response shape (``MessageResponse`` with
``content: tuple[ContentBlock, ...]``).
"""
from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from llm_code.api.types import (
    MessageRequest,
    MessageResponse,
    StreamEvent,
    StreamMessageStart,
    StreamMessageStop,
    StreamTextDelta,
    TextBlock,
    TokenUsage,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationHit:
    """A successful detector match.

    Carries the detector's ``name`` (for telemetry) plus the synthetic
    ``response`` to return without an HTTP round-trip.
    """
    name: str
    response: MessageResponse


# ── Helper utilities ──────────────────────────────────────────────────


_LISTING_COMMANDS: frozenset[str] = frozenset({
    "ls", "dir", "find", "tree", "pwd", "cd", "mkdir", "rmdir", "rm",
})

_READING_COMMANDS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more", "bat", "type",
})

# Two-word command set — the first non-flag argument is part of the
# canonical "prefix" (e.g. ``git commit`` not just ``git``).
_TWO_WORD_COMMANDS: frozenset[str] = frozenset({
    "git", "npm", "docker", "kubectl", "cargo", "go", "pip", "yarn",
})


def _last_user_text(request: MessageRequest) -> str:
    """Concatenate all text blocks of the last user message.

    Walks ``messages`` from the end and returns the first
    ``role == "user"`` message's TextBlock contents joined together.
    Empty string if no user message exists.
    """
    for msg in reversed(request.messages):
        if msg.role != "user":
            continue
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            # Tool results are messages too in our shape; we don't
            # try to detect on those.
        return "".join(parts)
    return ""


def _all_user_text(request: MessageRequest) -> str:
    """Concatenate text from all user messages — used by detectors that
    look for a marker anywhere in the conversation rather than just
    the last user turn.
    """
    parts: list[str] = []
    for msg in request.messages:
        if msg.role != "user":
            continue
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
    return "".join(parts)


def _system_text(request: MessageRequest) -> str:
    """Return the request's system prompt text, lowercased.

    ``MessageRequest.system`` is ``str | None``. ``None`` returns the
    empty string so callers can do plain substring checks without
    None-guarding.
    """
    return (request.system or "").lower()


def _synthesize_response(
    text: str,
    *,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str = "end_turn",
) -> MessageResponse:
    """Build a synthetic ``MessageResponse`` with a single text block.

    Used by every detector. The token estimates are intentionally rough
    (~100 in / 5 out for most patterns) so the request-optimizations
    metric path still flows through the cost meter even when no real
    HTTP call happened.
    """
    return MessageResponse(
        content=(TextBlock(text=text),),
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        stop_reason=stop_reason,
    )


def _extract_command_prefix(command: str) -> str:
    """Local replacement for an LLM call that classifies a shell command.

    Matches the reference impl's behaviour: refuse anything containing
    backticks or ``$(...)`` (treats them as injection bait), then
    use ``shlex`` to split the command, walk past leading
    ``KEY=VALUE`` env-var prefixes, and return either ``CMD`` or
    ``CMD SUBCMD`` depending on whether the base command is in the
    two-word set (``git commit``, ``npm install``, etc.).
    """
    if "`" in command or "$(" in command:
        return "command_injection_detected"

    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return command.split()[0] if command.split() else "none"
    if not parts:
        return "none"

    env_prefix: list[str] = []
    cmd_start = 0
    for i, part in enumerate(parts):
        if "=" in part and not part.startswith("-"):
            env_prefix.append(part)
            cmd_start = i + 1
        else:
            break

    if cmd_start >= len(parts):
        return "none"

    cmd_parts = parts[cmd_start:]
    if not cmd_parts:
        return "none"

    first_word = cmd_parts[0]
    if first_word in _TWO_WORD_COMMANDS and len(cmd_parts) > 1:
        second = cmd_parts[1]
        if not second.startswith("-"):
            return f"{first_word} {second}"
        return first_word
    if env_prefix:
        return " ".join(env_prefix) + " " + first_word
    return first_word


def _extract_filepaths(command: str, output: str) -> str:
    """Locally answer "which file paths did this command read?".

    Mirrors the reference impl's `<filepaths>...</filepaths>` shape so
    the caller's downstream processing is identical to a real model
    response. Unknown commands return an empty `<filepaths>` block
    rather than guessing — this is the conservative choice that
    avoids emitting a false positive that gets fed back to the agent.
    """
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return "<filepaths>\n</filepaths>"
    if not parts:
        return "<filepaths>\n</filepaths>"

    base_cmd = parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()

    if base_cmd in _LISTING_COMMANDS:
        return "<filepaths>\n</filepaths>"

    if base_cmd in _READING_COMMANDS:
        filepaths: list[str] = [p for p in parts[1:] if not p.startswith("-")]
        if filepaths:
            return "<filepaths>\n" + "\n".join(filepaths) + "\n</filepaths>"
        return "<filepaths>\n</filepaths>"

    if base_cmd == "grep":
        # grep needs special handling: the FIRST positional argument is
        # the pattern unless ``-e`` / ``-f`` was passed earlier (in which
        # case all positional arguments are paths).
        flags_with_args = {"-e", "-f", "-m", "-A", "-B", "-C"}
        pattern_provided_via_flag = False
        positional: list[str] = []
        skip_next = False
        for part in parts[1:]:
            if skip_next:
                skip_next = False
                continue
            if part.startswith("-"):
                if part in flags_with_args:
                    if part in {"-e", "-f"}:
                        pattern_provided_via_flag = True
                    skip_next = True
                continue
            positional.append(part)
        filepaths = positional if pattern_provided_via_flag else positional[1:]
        if filepaths:
            return "<filepaths>\n" + "\n".join(filepaths) + "\n</filepaths>"
        return "<filepaths>\n</filepaths>"

    return "<filepaths>\n</filepaths>"


# ── Detector predicates ───────────────────────────────────────────────


def _quota_mock(request: MessageRequest) -> MessageResponse | None:
    """Detector: quota probe.

    Trigger pattern (from the reference proxy and confirmed across
    Claude Code traffic):

    * ``max_tokens == 1``
    * Exactly one message in the conversation, role ``user``
    * The user message text contains the substring "quota"
      (case-insensitive)

    Response: a synthetic 1-token "Quota check passed." text block.
    """
    if request.max_tokens != 1:
        return None
    if len(request.messages) != 1:
        return None
    only_msg = request.messages[0]
    if only_msg.role != "user":
        return None
    text = "".join(
        b.text for b in only_msg.content if isinstance(b, TextBlock)
    )
    if "quota" not in text.lower():
        return None
    return _synthesize_response(
        "Quota check passed.",
        input_tokens=10,
        output_tokens=5,
    )


def _prefix_detection(request: MessageRequest) -> MessageResponse | None:
    """Detector: shell command prefix detection.

    Trigger pattern:

    * Single user message
    * Body contains both ``<policy_spec>`` and ``Command:``

    Response: parse the command after ``Command:``, extract its prefix
    locally via ``shlex``, return that string.
    """
    if len(request.messages) != 1:
        return None
    only_msg = request.messages[0]
    if only_msg.role != "user":
        return None
    text = "".join(
        b.text for b in only_msg.content if isinstance(b, TextBlock)
    )
    if "<policy_spec>" not in text or "Command:" not in text:
        return None
    cmd_start = text.rfind("Command:") + len("Command:")
    command = text[cmd_start:].strip()
    if not command:
        return None
    return _synthesize_response(
        _extract_command_prefix(command),
        input_tokens=100,
        output_tokens=5,
    )


def _title_skip(request: MessageRequest) -> MessageResponse | None:
    """Detector: conversation title generation.

    Trigger pattern:

    * Has a system prompt
    * No tools defined
    * System mentions "title" AND ("sentence-case title" OR
      ("return json" + "field" + ("coding session" OR "this session"))).

    The compound second branch matches Claude Code's title-extraction
    prompt phrasing ("Return JSON with a single 'title' field
    summarizing this coding session…").

    Response: ``"Conversation"`` (Claude Code's documented fallback
    string).
    """
    if not request.system or request.tools:
        return None
    sys_text = _system_text(request)
    if "title" not in sys_text:
        return None
    if "sentence-case title" in sys_text or (
        "return json" in sys_text
        and "field" in sys_text
        and ("coding session" in sys_text or "this session" in sys_text)
    ):
        return _synthesize_response(
            "Conversation",
            input_tokens=100,
            output_tokens=5,
        )
    return None


def _suggestion_skip(request: MessageRequest) -> MessageResponse | None:
    """Detector: agent suggestion-mode probe.

    Trigger pattern: any user message contains the literal substring
    ``[SUGGESTION MODE:`` (Claude Code uses this marker to ask the
    model to generate user-input completions for its own UI).

    Response: empty text (the suggestion is "no suggestion").
    """
    text = _all_user_text(request)
    if "[SUGGESTION MODE:" not in text:
        return None
    return _synthesize_response(
        "",
        input_tokens=100,
        output_tokens=1,
    )


# Pre-compiled regexes used across multiple detector calls.
_FILEPATH_TAG_RE = re.compile(r"<filepaths>", re.IGNORECASE)


def _filepath_mock(request: MessageRequest) -> MessageResponse | None:
    """Detector: filepath-extraction request.

    Trigger pattern:

    * Single user message, no tools defined
    * Body contains both ``Command:`` and ``Output:``
    * Either the user body or the system prompt explicitly asks for
      filepath extraction (lowercase substring "filepaths" /
      ``<filepaths>`` in the user body, or "extract any file paths"
      in the system prompt).

    Response: parse the Command + Output locally, extract paths via
    ``shlex``-aware logic, return the ``<filepaths>...</filepaths>``
    block string.
    """
    if len(request.messages) != 1:
        return None
    only_msg = request.messages[0]
    if only_msg.role != "user":
        return None
    if request.tools:
        return None
    text = "".join(
        b.text for b in only_msg.content if isinstance(b, TextBlock)
    )
    if "Command:" not in text or "Output:" not in text:
        return None
    user_has_filepaths = (
        "filepaths" in text.lower() or _FILEPATH_TAG_RE.search(text) is not None
    )
    sys_text = _system_text(request)
    sys_has_extract = (
        "extract any file paths" in sys_text
        or "file paths that this command" in sys_text
    )
    if not user_has_filepaths and not sys_has_extract:
        return None

    cmd_start = text.find("Command:") + len("Command:")
    output_marker = text.find("Output:", cmd_start)
    if output_marker == -1:
        return None

    command = text[cmd_start:output_marker].strip()
    output = text[output_marker + len("Output:"):].strip()

    # Truncate output at the next angle bracket or blank line — matches
    # the reference proxy's defensive trimming so a trailing system tag
    # doesn't bleed into the path-extraction logic.
    for marker in ("<", "\n\n"):
        if marker in output:
            output = output.split(marker)[0].strip()

    return _synthesize_response(
        _extract_filepaths(command, output),
        input_tokens=100,
        output_tokens=10,
    )


# ── Registry + walker ─────────────────────────────────────────────────


_Detector = Callable[[MessageRequest], "MessageResponse | None"]

# Registry order — cheapest/most-specific first so we short-circuit
# fast on the common case. Quota mocks are O(1); title skip is the
# most-frequently-hit pattern in real Claude Code traffic.
_DETECTORS: tuple[tuple[str, _Detector], ...] = (
    ("quota_mock", _quota_mock),
    ("prefix_detection", _prefix_detection),
    ("title_skip", _title_skip),
    ("suggestion_skip", _suggestion_skip),
    ("filepath_mock", _filepath_mock),
)


def try_optimize(request: MessageRequest) -> OptimizationHit | None:
    """Run detectors in registry order. Return the first hit or ``None``.

    Callers should gate the call themselves on the active profile's
    ``enable_request_optimizations`` flag — we don't read profile state
    here so the function stays pure / unit-testable in isolation.
    """
    for name, detector in _DETECTORS:
        response = detector(request)
        if response is not None:
            _logger.info(
                "request_optimizations: hit name=%s saved_input_tokens=~%d",
                name, response.usage.input_tokens,
            )
            return OptimizationHit(name=name, response=response)
    return None


# ── Streaming wrapper ─────────────────────────────────────────────────


async def _synthesize_stream_events(
    response: MessageResponse,
) -> AsyncIterator[StreamEvent]:
    """Emit a ``MessageResponse`` as a one-shot event stream.

    Producers of synthetic responses (``try_optimize`` hits) need to
    supply a stream-shaped result for ``stream_message`` callers. This
    helper yields:

    1. One :class:`StreamMessageStart`
    2. One :class:`StreamTextDelta` per :class:`TextBlock`
       in the response
    3. One :class:`StreamMessageStop` with the response's usage and
       stop reason

    Both providers' downstream renderers tolerate this minimal shape
    (no thinking blocks, no tool blocks — by construction synthetic
    responses are plain text).

    The ``yield`` statements run inside an ``async`` generator so the
    iterator is properly typed as ``AsyncIterator[StreamEvent]``.
    """
    yield StreamMessageStart(model="")
    for block in response.content:
        if isinstance(block, TextBlock) and block.text:
            yield StreamTextDelta(text=block.text)
    yield StreamMessageStop(
        usage=response.usage,
        stop_reason=response.stop_reason,
    )

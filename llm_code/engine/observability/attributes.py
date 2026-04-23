"""Canonical span attribute names for llmcode.

These constants are the *only* attribute keys the engine is permitted to
set on spans. ``ALLOWED_ATTRIBUTE_KEYS`` enumerates them as a frozenset
and ``set_attr_safe`` rejects anything else at runtime — this prevents
drift in the span schema as new call sites are added across the codebase.

Names follow the OpenTelemetry semantic conventions for GenAI
(`gen_ai.*` namespace, see
https://opentelemetry.io/docs/specs/semconv/attributes-registry/gen-ai/)
for model/API attributes; llmcode-specific attributes sit under the
``llmcode.*`` namespace. Any new attribute must be added here *and*
documented in ``docs/engine/observability_attribute_reference.md``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# ---------------------------------------------------------------------------
# Pipeline / Component
# ---------------------------------------------------------------------------
PIPELINE_NAME = "llmcode.pipeline.name"
COMPONENT_NAME = "llmcode.component.name"
COMPONENT_INPUT_KEYS = "llmcode.component.input_keys"
COMPONENT_OUTPUT_KEYS = "llmcode.component.output_keys"

# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
AGENT_ITERATION = "llmcode.agent.iteration"
AGENT_MODE = "llmcode.agent.mode"
AGENT_EXIT_REASON = "llmcode.agent.exit_reason"
AGENT_DEGRADED = "llmcode.agent.degraded"

# ---------------------------------------------------------------------------
# Tool call
# ---------------------------------------------------------------------------
TOOL_NAME = "llmcode.tool.name"
TOOL_ARGS_HASH = "llmcode.tool.args_hash"   # sha256 (truncated) — never raw args
TOOL_RESULT_IS_ERROR = "llmcode.tool.result_is_error"
TOOL_RETRY_ATTEMPT = "llmcode.tool.retry_attempt"
TOOL_FALLBACK_FROM = "llmcode.tool.fallback_from"

# ---------------------------------------------------------------------------
# Model / API — OpenTelemetry GenAI semantic conventions
# ---------------------------------------------------------------------------
MODEL_NAME = "gen_ai.request.model"
MODEL_PROVIDER = "gen_ai.system"
TOKENS_IN = "gen_ai.usage.prompt_tokens"
TOKENS_OUT = "gen_ai.usage.completion_tokens"
MODEL_TEMPERATURE = "gen_ai.request.temperature"

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION_ID = "llmcode.session.id"
USER_PROMPT_LENGTH = "llmcode.user_prompt.length"  # length in chars — never the content


# ---------------------------------------------------------------------------
# Allow-list
# ---------------------------------------------------------------------------
ALLOWED_ATTRIBUTE_KEYS: frozenset[str] = frozenset({
    # pipeline / component
    PIPELINE_NAME,
    COMPONENT_NAME,
    COMPONENT_INPUT_KEYS,
    COMPONENT_OUTPUT_KEYS,
    # agent
    AGENT_ITERATION,
    AGENT_MODE,
    AGENT_EXIT_REASON,
    AGENT_DEGRADED,
    # tool
    TOOL_NAME,
    TOOL_ARGS_HASH,
    TOOL_RESULT_IS_ERROR,
    TOOL_RETRY_ATTEMPT,
    TOOL_FALLBACK_FROM,
    # model / api
    MODEL_NAME,
    MODEL_PROVIDER,
    TOKENS_IN,
    TOKENS_OUT,
    MODEL_TEMPERATURE,
    # session
    SESSION_ID,
    USER_PROMPT_LENGTH,
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def args_hash(args: dict) -> str:
    """Return a 16-char SHA-256 hex digest of ``args``.

    Used for the ``llmcode.tool.args_hash`` attribute — we never put the
    raw arguments on a span (PII risk). ``json.dumps(..., sort_keys=True,
    default=str)`` normalises ordering and handles non-JSON values.
    """
    payload = json.dumps(args, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def set_attr_safe(span: Any, key: str, value: Any) -> None:
    """Set ``key=value`` on ``span`` only if ``key`` is in the allow-list.

    * Unknown keys raise :class:`ValueError` — this guards against
      hand-rolled attribute names leaking into traces.
    * ``span is None`` is treated as a no-op, which matches the
      behaviour of :mod:`llm_code.engine.tracing` when OpenTelemetry is
      not installed and tracing context managers yield ``None``.
    """
    if key not in ALLOWED_ATTRIBUTE_KEYS:
        raise ValueError(
            f"attribute key {key!r} not in ALLOWED_ATTRIBUTE_KEYS; add it to "
            "llm_code.engine.observability.attributes before setting it on a span"
        )
    if span is None:
        return
    span.set_attribute(key, value)

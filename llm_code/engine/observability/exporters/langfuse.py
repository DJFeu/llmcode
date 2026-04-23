"""Langfuse span exporter — translates OTel spans into Langfuse's
session/observation/generation model.

Langfuse semantic mapping (see
https://langfuse.com/docs/opentelemetry/get-started):

* A span whose :data:`SESSION_ID` attribute is set starts/updates a
  Langfuse session.
* ``tool.*`` / ``component.*`` / ``pipeline.*`` spans become Langfuse
  **spans**.
* ``api.*`` spans with ``gen_ai.*`` attributes become Langfuse
  **generations** (carrying token usage + model name).
* Every other span is mapped to a generic span so trace fidelity is
  preserved.

The translator reads the canonical attribute names from
:mod:`llm_code.engine.observability.attributes`, so any attribute drift
is caught by the attribute allow-list test.

When the ``langfuse`` package is not installed, :func:`build_langfuse_exporter`
raises :class:`ImportError`; the top-level factory catches it and
disables the Langfuse branch.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from llm_code.engine.observability.attributes import (
    AGENT_EXIT_REASON,
    AGENT_ITERATION,
    AGENT_MODE,
    COMPONENT_NAME,
    MODEL_NAME,
    MODEL_PROVIDER,
    MODEL_TEMPERATURE,
    PIPELINE_NAME,
    SESSION_ID,
    TOKENS_IN,
    TOKENS_OUT,
    TOOL_NAME,
    TOOL_RESULT_IS_ERROR,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional probe
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
        SpanExporter,
        SpanExportResult,
    )

    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment,misc]
    _OTEL_SDK_AVAILABLE = False


def _attrs(span: Any) -> dict:
    """Return a plain dict of span attributes; tolerate missing/weird attrs."""
    try:
        return dict(getattr(span, "attributes", {}) or {})
    except Exception:  # pragma: no cover - defensive
        return {}


def _span_kind(span: Any) -> str:
    """Classify an OTel span into one of Langfuse's concepts."""
    attrs = _attrs(span)
    if TOKENS_IN in attrs or TOKENS_OUT in attrs or MODEL_NAME in attrs:
        return "generation"
    if TOOL_NAME in attrs:
        return "tool"
    if COMPONENT_NAME in attrs:
        return "component"
    if PIPELINE_NAME in attrs:
        return "pipeline"
    if AGENT_ITERATION in attrs or AGENT_MODE in attrs:
        return "agent"
    return "span"


class LangfuseSpanExporter(SpanExporter):  # type: ignore[misc]
    """Translate OTel :class:`ReadableSpan` instances into Langfuse
    observations.

    ``langfuse_client`` is a pre-configured ``langfuse.Langfuse``
    instance. The exporter does not own the client's lifecycle — the
    caller flushes/shuts it down (Langfuse's client manages its own
    background worker).
    """

    def __init__(self, langfuse_client: Any) -> None:
        self._lf = langfuse_client

    # ----- OTel SpanExporter protocol ---------------------------------------
    def export(self, spans: Iterable[Any]) -> Any:  # noqa: D401
        for span in spans:
            try:
                self._export_one(span)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("langfuse export failed for span: %s", exc)
        if _OTEL_SDK_AVAILABLE:
            return SpanExportResult.SUCCESS
        return None

    def shutdown(self) -> None:
        flush = getattr(self._lf, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception:  # pragma: no cover - defensive
                pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        self.shutdown()
        return True

    # ----- translation ------------------------------------------------------
    def _export_one(self, span: Any) -> None:
        """Translate a single OTel span into a Langfuse observation."""
        attrs = _attrs(span)
        kind = _span_kind(span)
        name = getattr(span, "name", "span")
        session_id = attrs.get(SESSION_ID)

        payload: dict[str, Any] = {
            "name": name,
            "metadata": {k: v for k, v in attrs.items()},
        }
        if session_id:
            payload["session_id"] = session_id

        start = getattr(span, "start_time", None)
        end = getattr(span, "end_time", None)
        if start is not None:
            payload["start_time"] = _ns_to_seconds(start)
        if end is not None:
            payload["end_time"] = _ns_to_seconds(end)

        if kind == "generation":
            self._emit_generation(payload, attrs)
            return
        self._emit_span(payload, attrs)

    def _emit_generation(self, payload: dict, attrs: dict) -> None:
        """Map a model/API span to ``Langfuse.generation``."""
        model = attrs.get(MODEL_NAME)
        provider = attrs.get(MODEL_PROVIDER)
        temperature = attrs.get(MODEL_TEMPERATURE)
        tokens_in = attrs.get(TOKENS_IN)
        tokens_out = attrs.get(TOKENS_OUT)

        payload["model"] = model
        if provider:
            payload["model_parameters"] = {"provider": provider}
        if temperature is not None:
            payload.setdefault("model_parameters", {})["temperature"] = temperature
        if tokens_in is not None or tokens_out is not None:
            payload["usage"] = {
                "input": tokens_in or 0,
                "output": tokens_out or 0,
                "total": (tokens_in or 0) + (tokens_out or 0),
            }

        generation = getattr(self._lf, "generation", None)
        if callable(generation):
            generation(**payload)
        else:
            # Older/newer Langfuse SDKs expose different method names;
            # fall back to a generic ``span`` call so we don't lose data.
            self._emit_span(payload, attrs)

    def _emit_span(self, payload: dict, attrs: dict) -> None:
        """Map a non-model span to ``Langfuse.span``."""
        if attrs.get(TOOL_NAME):
            payload.setdefault("metadata", {})["tool"] = attrs.get(TOOL_NAME)
            if attrs.get(TOOL_RESULT_IS_ERROR):
                payload["level"] = "ERROR"
        if attrs.get(AGENT_EXIT_REASON):
            payload.setdefault("metadata", {})["exit_reason"] = attrs.get(
                AGENT_EXIT_REASON
            )
        span_fn = getattr(self._lf, "span", None)
        if callable(span_fn):
            span_fn(**payload)


def _ns_to_seconds(ns: int) -> float:
    return float(ns) / 1e9


def build_langfuse_exporter(config: Any) -> LangfuseSpanExporter:
    """Read credentials from the env (names configurable via ``config``)
    and return a :class:`LangfuseSpanExporter`.

    Raises :class:`ImportError` if the ``langfuse`` package is missing
    so the top-level factory can disable the branch cleanly.
    """
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - surfaced to factory
        raise ImportError("langfuse package not installed") from exc

    public_env = getattr(config, "langfuse_public_key_env", "LANGFUSE_PUBLIC_KEY")
    secret_env = getattr(config, "langfuse_secret_key_env", "LANGFUSE_SECRET_KEY")
    host = getattr(config, "langfuse_host", "https://cloud.langfuse.com")

    public_key = os.environ.get(public_env, "")
    secret_key = os.environ.get(secret_env, "")

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    return LangfuseSpanExporter(client)


__all__ = [
    "LangfuseSpanExporter",
    "build_langfuse_exporter",
    "_span_kind",
]

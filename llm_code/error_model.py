"""Unified error model for llm-code (H6 — Sprint 3).

Inspired by Codex's ``codex-core/execpolicy/src/error.rs`` — a single
structured error type with source-location context, a coded identifier
for programmatic handling, and a free-form context dict for operational
metadata.

Naming note
-----------

``llm_code.api.errors.LLMCodeError`` (pre-existing) is the **runtime
exception hierarchy base** — catch it to trap every tool/provider
failure.

``llm_code.error_model.LLMCodeError`` (this module) is the **structured
wire-format error** — a frozen dataclass that both *is* an Exception
and carries a JSON-safe dict for audit / SDK / ``/diagnose`` output.
Different namespace, different purpose; the two co-exist. Ambient
imports in this repo canonically prefer ``from llm_code.error_model``
for the dataclass.

Goals:

    * One type to raise when the runtime needs to surface a structured
      error to the caller (SDK response, ``/diagnose`` output, audit
      log) — everything JSON-safe.
    * Fluent ``with_location`` / ``with_context`` helpers that never
      mutate the base error; chaining produces new instances.
    * Ordered :class:`ErrorSeverity` so callers can filter by minimum
      severity (``errors = [e for e in errs if e.severity >= WARNING]``).
    * :func:`from_provider_exception` bridge — marshals any
      ``api.errors.ProviderError`` into this model so transport
      failures share the same envelope as tool failures.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any


class ErrorSeverity(IntEnum):
    """Ordered severity — ``INFO < WARNING < ERROR < FATAL``.

    IntEnum makes comparison cheap (``severity >= WARNING``) while
    ``.name`` gives a stable lowercase string for JSON output via the
    :attr:`value` property below.
    """
    INFO = 10
    WARNING = 20
    ERROR = 30
    FATAL = 40

    @property
    def value(self) -> str:  # type: ignore[override]
        return self.name.lower()


@dataclass(frozen=True)
class SourceLocation:
    """Where in a source file the error was triggered."""
    file_path: str
    line: int | None = None
    column: int | None = None
    line_text: str = ""

    def format(self) -> str:
        """Human-readable ``file:line:column`` string."""
        if self.line is None:
            return self.file_path
        parts = [self.file_path, str(self.line)]
        if self.column is not None:
            parts.append(str(self.column))
        return ":".join(parts)


@dataclass(frozen=True)
class LLMCodeError(Exception):
    """Structured error that flows through the surface layer.

    ``code`` identifies the error family (``E_PATCH_FAIL``,
    ``E_SANDBOX_DENIED``, ...) so callers can branch on it
    programmatically; ``message`` is a human-readable one-liner;
    ``context`` is a free-form JSON-safe dict for operational metadata.
    """
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    location: SourceLocation | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataclass __init__ doesn't call ``Exception.__init__`` so
        # ``str(err)`` would return ``""``. Wire it up manually.
        Exception.__init__(self, self.message)

    def __str__(self) -> str:
        if self.location is not None:
            return f"[{self.code}] {self.location.format()}: {self.message}"
        return f"[{self.code}] {self.message}"

    def with_location(self, location: SourceLocation) -> "LLMCodeError":
        """Return a copy with ``location`` set; original untouched."""
        return replace(self, location=location)

    def with_context(self, **extras: Any) -> "LLMCodeError":
        """Return a copy with ``extras`` merged into ``context``."""
        merged = dict(self.context)
        merged.update(extras)
        return replace(self, context=merged)

    def to_dict(self) -> dict[str, Any]:
        loc: dict[str, Any] | None = None
        if self.location is not None:
            loc = {
                "file_path": self.location.file_path,
                "line": self.location.line,
                "column": self.location.column,
                "line_text": self.location.line_text,
            }
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "location": loc,
            "context": dict(self.context),
        }

    def to_tool_metadata(self) -> dict[str, Any]:
        """Return a metadata dict ready for ``ToolResult.metadata``.

        Wraps :meth:`to_dict` under the ``llmcode_error`` key so
        existing metadata shape is preserved — tools that set other
        keys (diff hunks, file mtime, ...) merge this dict without
        clobbering anything.
        """
        return {"llmcode_error": self.to_dict()}


# ── Provider-exception bridge (S4.4) ──────────────────────────────────

_PROVIDER_EXC_CODES: dict[str, str] = {
    "ProviderAuthError": "E_PROVIDER_AUTH",
    "ProviderModelNotFoundError": "E_PROVIDER_MODEL_NOT_FOUND",
    "ProviderRateLimitError": "E_PROVIDER_RATE_LIMIT",
    "ProviderOverloadError": "E_PROVIDER_OVERLOAD",
    "ProviderTimeoutError": "E_PROVIDER_TIMEOUT",
    "ProviderConnectionError": "E_PROVIDER_CONNECTION",
    "ProviderError": "E_PROVIDER",
}

# Auth + model-not-found will never self-heal on retry — callers
# should surface them directly instead of silently hammering retry.
_PERMANENT_EXC_NAMES = frozenset({"ProviderAuthError", "ProviderModelNotFoundError"})


def from_provider_exception(
    exc: BaseException,
    *,
    base_url: str = "",
    model: str = "",
    **extra_context: Any,
) -> "LLMCodeError":
    """Wrap a provider-side exception into a structured :class:`LLMCodeError`.

    Works with both the concrete ``api.errors.Provider*`` hierarchy and
    arbitrary exceptions (which fall back to ``E_PROVIDER_UNKNOWN``).
    Severity escalates to FATAL for permanent failures (auth, unknown
    model) so callers can branch on ``severity >= FATAL`` to stop
    automated retry loops.

    Empty-string / None context values are dropped from the resulting
    dict to keep the JSON wire format tidy.
    """
    exc_name = type(exc).__name__
    code = _PROVIDER_EXC_CODES.get(exc_name, "E_PROVIDER_UNKNOWN")
    severity = (
        ErrorSeverity.FATAL if exc_name in _PERMANENT_EXC_NAMES else ErrorSeverity.ERROR
    )

    context: dict[str, Any] = {
        "exception_type": exc_name,
        "base_url": base_url,
        "model": model,
        "retry_after": getattr(exc, "retry_after", None),
        "is_retryable": getattr(exc, "is_retryable", None),
    }
    context.update(extra_context)
    context = {
        k: v for k, v in context.items()
        if v is not None and v != ""
    }

    return LLMCodeError(
        code=code,
        message=str(exc),
        severity=severity,
        context=context,
    )

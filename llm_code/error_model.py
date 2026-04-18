"""Unified error model for llm-code (H6 — Sprint 3).

Inspired by Codex's ``codex-core/execpolicy/src/error.rs`` — a single
structured error type with source-location context, a coded identifier
for programmatic handling, and a free-form context dict for operational
metadata.

Goals:

    * One type to raise when the runtime needs to surface a structured
      error to the caller (SDK response, ``/diagnose`` output, audit
      log) — everything JSON-safe.
    * Fluent ``with_location`` / ``with_context`` helpers that never
      mutate the base error; chaining produces new instances.
    * Ordered :class:`ErrorSeverity` so callers can filter by minimum
      severity (``errors = [e for e in errs if e.severity >= WARNING]``).

Non-goals:

    * Replacing provider-side exceptions (``ProviderRateLimitError``
      etc.) — those stay as-is; the unified model is for the surface
      layer above them.
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

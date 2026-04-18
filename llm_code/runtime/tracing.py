"""OpenTelemetry-style tracing skeleton (M9).

When ``opentelemetry`` is installed in the host environment, a real
tracer is returned. Otherwise callers get a no-op implementation so
every call site stays safe to instrument without forcing a dependency.
"""
from __future__ import annotations

import functools
from typing import Any, Callable


class NoopSpan:
    """Span that silently accepts every method from the OTel span API."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "NoopSpan":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ARG002
        return None

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        return None

    def add_event(self, name: str, attributes: dict | None = None) -> None:  # noqa: ARG002
        return None

    def record_exception(self, exception: BaseException) -> None:  # noqa: ARG002
        return None

    def set_status(self, status_code: str, description: str = "") -> None:  # noqa: ARG002
        return None


class NoopTracer:
    def start_span(self, name: str) -> NoopSpan:
        return NoopSpan(name)


def get_tracer():
    """Return the best tracer available.

    Tries OpenTelemetry first; falls back to :class:`NoopTracer` when
    the package isn't installed — callers never have to check.
    """
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        return trace.get_tracer("llm_code")
    except ImportError:
        return NoopTracer()


def instrument_tool(tool_name: str) -> Callable:
    """Decorator: wrap a tool ``execute`` function in a traced span."""
    tracer = get_tracer()

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            span = tracer.start_span(f"tool.{tool_name}")
            with span:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if hasattr(span, "record_exception"):
                        try:
                            span.record_exception(exc)
                        except Exception:
                            pass
                    raise
        return wrapper
    return decorator

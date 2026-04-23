"""Trace context propagation across sync <-> async boundaries.

OpenTelemetry spans are tied to the current execution context. When we
hop between asyncio and threads (``asyncio.to_thread``), launch a
sub-agent, or cross a SSE streaming boundary, the active context needs
to be captured on one side and re-applied on the other so the child
span has the correct parent and the resulting trace tree is not
fragmented.

This module provides three building blocks:

* A :class:`contextvars.ContextVar` ``_trace_ctx`` holding the
  carrier-style snapshot that callers want to propagate. Functions
  keep using the OTel APIs for the active span (``current_span()``);
  ``_trace_ctx`` is the *explicit* hand-off slot.
* :func:`propagate_across_to_thread` — a context manager that captures
  the current OTel context and exposes the resulting token through
  ``as``. Intended callsite::

      with propagate_across_to_thread() as token:
          await asyncio.to_thread(sync_worker, token)

* :func:`apply_context` — the receiving side. Given the token yielded
  by :func:`propagate_across_to_thread`, attach the captured context
  for the duration of the ``with`` block::

      def sync_worker(token):
          with apply_context(token):
              ...  # spans opened here are children of the caller

OpenTelemetry is an **optional dependency**. When the ``opentelemetry``
package cannot be imported every helper here degrades to a no-op:
context managers yield immediately, :func:`inject_parent_into_span`
returns ``None``. Call sites in the engine therefore do not have to
guard every invocation with ``if OTEL_INSTALLED:``.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Optional

try:  # pragma: no cover - optional dep probe
    from opentelemetry import context as _otel_context  # type: ignore[import-not-found]
    from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep fallback
    _otel_context = None
    _otel_trace = None
    _OTEL_AVAILABLE = False


# The ContextVar carries the most recently captured OTel ``Context``
# (or ``None`` when unset). The default is ``None`` — readers MUST
# handle that case so unreferenced workers keep working.
_trace_ctx: ContextVar[Optional[Any]] = ContextVar(
    "llmcode_trace_ctx", default=None
)


# ---------------------------------------------------------------------------
# Read-side helpers
# ---------------------------------------------------------------------------
def current_span() -> Any:
    """Return the currently active OTel span or ``None``.

    Wraps ``opentelemetry.trace.get_current_span``; returns ``None`` if
    OTel is missing so callers can use the result unconditionally.
    """
    if not _OTEL_AVAILABLE:
        return None
    return _otel_trace.get_current_span()


def get_context() -> Any:
    """Return the currently stashed context (or ``None``).

    This reads the :data:`_trace_ctx` ``ContextVar`` — distinct from the
    OTel active context, which is held by the SDK. Useful for tests or
    diagnostics that want to verify a propagation captured something.
    """
    return _trace_ctx.get()


def set_context(ctx: Any) -> Token:
    """Push ``ctx`` onto :data:`_trace_ctx` and return the reset token.

    The token must later be passed to :func:`reset_context` to restore
    the previous value. Callers typically use :func:`apply_context`
    instead, which manages the token via a ``with`` block.
    """
    return _trace_ctx.set(ctx)


def reset_context(token: Token) -> None:
    """Pop the last :func:`set_context` write."""
    _trace_ctx.reset(token)


# ---------------------------------------------------------------------------
# Propagation helpers
# ---------------------------------------------------------------------------
@contextmanager
def propagate_across_to_thread() -> Iterator[Any]:
    """Capture the current OTel context for hand-off to another thread.

    Yields a *token* (opaque; really an OTel ``Context`` object) that
    :func:`apply_context` knows how to re-attach on the receiving side.
    When OTel is missing, yields ``None`` — the caller can still pass
    it through opaquely, and :func:`apply_context` will no-op.

    Usage::

        async def caller():
            with propagate_across_to_thread() as token:
                await asyncio.to_thread(sync_worker, token)

        def sync_worker(token):
            with apply_context(token):
                ...  # spans created here have the caller's parent

    The context manager also stashes the captured context on
    :data:`_trace_ctx` so intermediate frames that can't easily take
    the token as an argument (e.g. framework callbacks) can read it
    via :func:`get_context`.
    """
    if not _OTEL_AVAILABLE:
        yield None
        return

    ctx = _otel_context.get_current()
    reset_token = _trace_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _trace_ctx.reset(reset_token)


@contextmanager
def apply_context(ctx: Any) -> Iterator[None]:
    """Attach ``ctx`` (captured by :func:`propagate_across_to_thread`)
    for the duration of the ``with`` block.

    If ``ctx`` is ``None`` (OTel missing, or the caller passed ``None``
    explicitly) the manager is a no-op — the block executes normally,
    and no context detaching is attempted. This lets sync worker code
    be written unconditionally::

        def worker(ctx_token):
            with apply_context(ctx_token):
                ...  # safe whether OTel is installed or not
    """
    if not _OTEL_AVAILABLE or ctx is None:
        yield
        return

    token = _otel_context.attach(ctx)
    try:
        yield
    finally:
        _otel_context.detach(token)


# ---------------------------------------------------------------------------
# Sub-agent parent span injection
# ---------------------------------------------------------------------------
def inject_parent_into_span(span: Any, parent_span: Any) -> None:
    """Record the parent span id on ``span`` as a link.

    OTel already tracks the parent implicitly when the child is started
    inside the parent's active context — but sub-agent spawns cross an
    ``asyncio.to_thread`` or a subprocess boundary where the implicit
    link can be lost. This helper adds an explicit ``span.link`` so the
    trace visualiser can stitch the tree regardless of how the child
    span was started.

    When either argument is ``None`` (OTel missing, or parent not
    captured) the call is a no-op — the caller still gets a well-formed
    child span, just without the extra annotation.
    """
    if not _OTEL_AVAILABLE:
        return
    if span is None or parent_span is None:
        return

    try:
        parent_ctx = parent_span.get_span_context()
        # ``set_attribute`` is the widely-supported fallback; real Links
        # have to be added at span-start time which is awkward to
        # retrofit through a sub-agent launcher. Attribute-based
        # annotation is enough for the trace viewer to reconstruct.
        span.set_attribute("llmcode.agent.parent_span_id", format(parent_ctx.span_id, "016x"))
        span.set_attribute("llmcode.agent.parent_trace_id", format(parent_ctx.trace_id, "032x"))
    except Exception:  # pragma: no cover - defensive
        # Never let propagation errors leak into the agent control path.
        return


__all__ = [
    "_OTEL_AVAILABLE",
    "_trace_ctx",
    "apply_context",
    "current_span",
    "get_context",
    "inject_parent_into_span",
    "propagate_across_to_thread",
    "reset_context",
    "set_context",
]

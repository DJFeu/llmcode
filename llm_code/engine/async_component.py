"""@async_component decorator + sync/async bridge helpers (M5 — Task 5.2).

Every Component either defines ``run`` (sync) or ``run_async`` (async),
not both. The base ``@component`` decorator auto-injects the missing
side so the AsyncPipeline can always ``await component.run_async(...)``
and the legacy sync Pipeline can always call ``component.run(...)``.

Public API
----------

- :func:`async_component` — drop-in for :func:`~llm_code.engine.component.component`
  on async-native classes. Mostly a semantic marker; it flips
  ``__is_async_native__`` on the class so introspection can tell "this
  component's canonical side is ``run_async``".
- :func:`ensure_run_async` — idempotent helper to attach a sync → async
  bridge (``asyncio.to_thread`` wrapper) when the class defines only
  ``run``. Called automatically by ``@component``.
- :func:`ensure_run` — idempotent helper to attach an async → sync
  bridge when the class defines only ``run_async``. The bridge raises
  clearly when called from inside a running event loop so callers are
  forced to ``await`` the async path instead of deadlocking.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.5
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-async-pipeline.md Task 5.2
"""
from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Callable


# Sentinel attribute names — consumers may reference these directly.
_ATTR_BRIDGE_RUN_ASYNC = "__run_async_is_bridge__"
_ATTR_BRIDGE_RUN = "__run_is_bridge__"
_ATTR_ASYNC_NATIVE = "__is_async_native__"


def _is_async_callable(fn: Callable[..., Any]) -> bool:
    """Return True if ``fn`` is an ``async def``.

    Uses :func:`inspect.iscoroutinefunction` which correctly handles
    methods decorated by ``functools.wraps``. We deliberately do *not*
    accept "returns-awaitable" functions — bridging those would require
    speculative introspection at call time and we prefer explicit
    ``async def`` so mistakes surface at decoration time.
    """
    return inspect.iscoroutinefunction(fn)


def ensure_run_async(cls: type) -> type:
    """Attach a default ``run_async`` that bridges to sync ``run``.

    Only added when the class defines ``run`` but not ``run_async``.
    The bridge is marked with :data:`_ATTR_BRIDGE_RUN_ASYNC` so
    introspection + :func:`inspect.iscoroutinefunction` on
    ``cls.run_async`` remain truthful.
    """
    if not hasattr(cls, "run"):
        return cls
    existing = cls.__dict__.get("run_async")
    if existing is not None:
        return cls
    # Inheritance case: if ``run_async`` is inherited from a base that
    # actually defined it (i.e. not a bridge), do not override.
    inherited = getattr(cls, "run_async", None)
    if inherited is not None and not getattr(inherited, _ATTR_BRIDGE_RUN_ASYNC, False):
        if _is_async_callable(inherited):
            return cls

    async def _run_async_bridge(self, *args: Any, **kwargs: Any) -> Any:
        # asyncio.to_thread preserves argument binding and returns a
        # coroutine we must await. We pass ``self.run`` so subclass
        # overrides of ``run`` are honoured.
        return await asyncio.to_thread(self.run, *args, **kwargs)

    setattr(_run_async_bridge, _ATTR_BRIDGE_RUN_ASYNC, True)
    functools.update_wrapper(
        _run_async_bridge,
        getattr(cls, "run"),
        assigned=("__doc__",),  # avoid clobbering __name__ / __qualname__
        updated=(),
    )
    cls.run_async = _run_async_bridge  # type: ignore[attr-defined]
    return cls


def ensure_run(cls: type) -> type:
    """Attach a default ``run`` that bridges to async ``run_async``.

    Only added when the class defines ``run_async`` but not ``run``.
    The sync bridge calls :func:`asyncio.run` when there is no running
    event loop; when called from inside a running loop it raises
    ``RuntimeError`` so callers cannot silently deadlock.
    """
    if not hasattr(cls, "run_async"):
        return cls
    if cls.__dict__.get("run") is not None:
        return cls
    inherited = getattr(cls, "run", None)
    if inherited is not None and not getattr(inherited, _ATTR_BRIDGE_RUN, False):
        # A real ``run`` already exists further up the MRO — do nothing.
        return cls

    def _run_bridge(self, *args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop → safe to drive one synchronously.
            return asyncio.run(self.run_async(*args, **kwargs))
        raise RuntimeError(
            f"{type(self).__name__}.run() cannot be called from inside a "
            "running event loop — await run_async() instead."
        )

    setattr(_run_bridge, _ATTR_BRIDGE_RUN, True)
    functools.update_wrapper(
        _run_bridge,
        getattr(cls, "run_async"),
        assigned=("__doc__",),
        updated=(),
    )
    cls.run = _run_bridge  # type: ignore[attr-defined]
    return cls


def async_component(cls: type | None = None, /) -> type | Callable[[type], type]:
    """Mark a class as async-native and wire both sides of the bridge.

    Usage::

        from llm_code.engine.component import component, output_types
        from llm_code.engine.async_component import async_component

        @component
        @async_component
        @output_types(value=int)
        class MyAsyncIO:
            async def run_async(self, x: int) -> dict:
                await asyncio.sleep(0.01)
                return {"value": x + 1}

    The decorator:

    1. Validates that ``run_async`` exists and is ``async def``; raises
       :class:`TypeError` at decoration time if the author forgot to
       ``async def``.
    2. Sets :data:`_ATTR_ASYNC_NATIVE` = ``True``.
    3. Calls :func:`ensure_run` to synthesise the sync bridge.

    The underlying :func:`~llm_code.engine.component.component` decorator
    still needs to be applied (for socket introspection). Order-agnostic:
    either decorator can run first; both are idempotent.
    """
    def _apply(target: type) -> type:
        run_async = target.__dict__.get("run_async") or getattr(target, "run_async", None)
        if run_async is None:
            raise TypeError(
                f"@async_component requires {target.__name__}.run_async to be defined."
            )
        if not _is_async_callable(run_async):
            raise TypeError(
                f"@async_component requires {target.__name__}.run_async to be "
                f"`async def`; got {run_async!r}."
            )
        # Component authors SHOULD NOT also define ``run`` on the same
        # class — we tolerate an inherited / bridge ``run`` but reject a
        # freshly-written one because mixed semantics cause surprises.
        own_run = target.__dict__.get("run")
        if own_run is not None and not getattr(own_run, _ATTR_BRIDGE_RUN, False):
            raise TypeError(
                f"{target.__name__} cannot define both `run` and `run_async`; "
                "use @async_component for async-native classes and let the "
                "decorator synthesise the sync bridge."
            )
        setattr(target, _ATTR_ASYNC_NATIVE, True)
        return ensure_run(target)

    if cls is None:
        return _apply
    return _apply(cls)


def is_async_native(obj: Any) -> bool:
    """Return True iff the class was built with :func:`async_component`."""
    return bool(getattr(obj, _ATTR_ASYNC_NATIVE, False))


__all__ = [
    "async_component",
    "ensure_run",
    "ensure_run_async",
    "is_async_native",
]

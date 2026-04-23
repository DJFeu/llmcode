"""Component decorator + Socket type.

Borrowed shape: ``haystack/core/component/component.py`` — the idea of
introspecting a component's ``run()`` method to derive typed input/output
sockets. Not borrowed:

- sockets-as-separate-objects API (Haystack has ``InputSocket`` +
  ``OutputSocket`` classes) — we collapse to a single :class:`Socket`
  with a ``direction`` field.
- warm-up hooks / component serialisation — cut for v12 scope; they
  can be re-added per milestone without breaking public API.

Public API:
    Socket                 - frozen dataclass describing one I/O slot.
    component              - class decorator: attach inputs from run().
    output_types           - class decorator: attach outputs by name.
    state_reads            - class decorator: declare engine State reads.
    state_writes           - class decorator: declare engine State writes.
    is_component           - predicate; works on classes or instances.
    get_input_sockets      - dict of declared input sockets.
    get_output_sockets     - dict of declared output sockets.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.2 + 2.5
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Literal, get_type_hints

SocketDirection = Literal["input", "output"]


@dataclass(frozen=True)
class Socket:
    """One input or output slot on a Component.

    Attributes:
        name: Socket name — matches the ``run()`` parameter name (input)
            or the key supplied via :func:`output_types` (output).
        type: Declared type. ``typing.Any`` is permitted (lenient
            typechecking at :meth:`Pipeline.connect` time).
        direction: ``"input"`` or ``"output"``.
        required: Only meaningful on inputs — ``True`` if the parameter
            has no default value in ``run()``.
        default: For optional inputs, the default value (or ``None``
            if there isn't one). Output sockets always have default
            ``None``; consumers should not rely on it.
    """

    name: str
    type: type
    direction: SocketDirection
    required: bool = True
    default: Any = None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _introspect_run_inputs(cls: type) -> dict[str, Socket]:
    """Build input sockets from the type hints on ``cls.run`` / ``cls.run_async``.

    Sync-first policy: if the class defines its own ``run`` (not a
    bridge from :mod:`async_component`), introspect that. Otherwise
    fall back to ``run_async`` so async-native components still get
    proper input sockets.
    """
    sync_run = getattr(cls, "run", None)
    async_run = getattr(cls, "run_async", None)
    # Prefer sync when it's actually authored (not the async→sync bridge).
    if sync_run is not None and not getattr(sync_run, "__run_is_bridge__", False):
        target = sync_run
    elif async_run is not None:
        target = async_run
    else:
        target = sync_run  # will raise a useful error below
    sig = inspect.signature(target)
    try:
        hints = get_type_hints(target)
    except Exception:
        # `get_type_hints` can fail on forward refs that aren't
        # resolvable in the test module; fall back to raw annotations
        # (which are still strings under `from __future__ import
        # annotations`). For v12 scope we treat those as `Any`.
        hints = {}
    input_sockets: dict[str, Socket] = {}
    params = list(sig.parameters.items())
    # Skip the bound `self` parameter.
    if params and params[0][0] == "self":
        params = params[1:]
    for pname, param in params:
        hint = hints.get(pname, Any)
        required = param.default is param.empty
        default = None if required else param.default
        input_sockets[pname] = Socket(
            name=pname,
            type=hint,
            direction="input",
            required=required,
            default=default,
        )
    return input_sockets


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def component(cls: type) -> type:
    """Class decorator: introspect ``run()`` / ``run_async()`` and attach I/O sockets.

    The decorator wires three class attributes:

    - ``__component_inputs__``: ``dict[str, Socket]`` built from the
      parameters of ``run()`` (or ``run_async()`` when the class is
      async-native and defines no sync ``run``).
    - ``__component_outputs__``: ``dict[str, Socket]`` populated by
      :func:`output_types`. If ``@output_types`` has not been applied,
      this is an empty dict.
    - ``__is_component__`` = ``True``.

    The class must define a ``run(self, ...)`` *or* ``run_async(self, ...)``
    method. Missing both raises :class:`TypeError` at decoration time.

    **M5 async bridge:** after socket wiring, the decorator calls
    :func:`llm_code.engine.async_component.ensure_run_async` and
    :func:`~llm_code.engine.async_component.ensure_run` so that callers
    can always use the sync or async surface interchangeably. Sync-only
    components transparently gain an ``asyncio.to_thread`` bridge; async-
    only components gain a sync bridge that raises when invoked inside a
    running loop (to prevent deadlock).

    **M6 observability hook:** after the socket wiring, the class is
    also passed through :func:`llm_code.engine.tracing.traced_component`
    so every ``Component.run`` / ``run_async`` call opens a span. The
    tracing import is guarded — if :mod:`llm_code.engine.tracing` is not
    importable (deliberate test isolation, or a very partial install)
    we log and continue so engine import never fails because of
    observability.
    """
    has_run = "run" in getattr(cls, "__dict__", {}) or hasattr(cls, "run")
    has_run_async = "run_async" in getattr(cls, "__dict__", {}) or hasattr(cls, "run_async")
    if not has_run and not has_run_async:
        raise TypeError(f"{cls.__name__} must define a `run` or `run_async` method")
    cls.__component_inputs__ = _introspect_run_inputs(cls)  # type: ignore[attr-defined]
    # Outputs may have been set by a prior (inner) `@output_types` call.
    cls.__component_outputs__ = getattr(cls, "__component_outputs__", {})  # type: ignore[attr-defined]
    cls.__is_component__ = True  # type: ignore[attr-defined]

    # M5: auto-wire the async / sync bridges. Keep this BEFORE the
    # tracing wrapper so observability spans cover both sides of the
    # bridge without having to know which side was authored.
    try:
        from llm_code.engine.async_component import ensure_run, ensure_run_async

        cls = ensure_run_async(cls)
        cls = ensure_run(cls)
    except Exception:  # pragma: no cover - defensive
        pass

    # Apply the M6 tracing wrapper. The try/except keeps engine import
    # resilient to an observability-package import error (the module
    # exposes a no-op ``traced_component`` when OTel itself is missing,
    # but a catastrophic import failure still falls through here).
    try:
        from llm_code.engine.tracing import traced_component as _traced_component

        cls = _traced_component(cls)
    except Exception:  # pragma: no cover - defensive
        pass
    return cls


def output_types(**types: type) -> Callable[[type], type]:
    """Class decorator factory: declare the output sockets of a Component.

    Usage::

        @component
        @output_types(allowed=bool, reason=str)
        class MyCheck:
            def run(self, ...) -> dict:
                ...
                return {"allowed": True, "reason": ""}

    The order of ``@component`` and ``@output_types`` is flexible: either
    produces a class with the same ``__component_inputs__`` and
    ``__component_outputs__`` attributes.
    """

    def _wrap(cls: type) -> type:
        sockets = {
            n: Socket(name=n, type=t, direction="output", required=True)
            for n, t in types.items()
        }
        cls.__component_outputs__ = sockets  # type: ignore[attr-defined]
        return cls

    return _wrap


def state_reads(*keys: str) -> Callable[[type], type]:
    """Class decorator: declare which engine State keys this Component reads.

    Stored on the class as ``__state_reads__: frozenset[str]``. Consumed
    by :meth:`Pipeline.validate` to reason about read-after-write
    ordering in future milestones; today it is mostly informational.
    """

    def _wrap(cls: type) -> type:
        cls.__state_reads__ = frozenset(keys)  # type: ignore[attr-defined]
        return cls

    return _wrap


def state_writes(*keys: str) -> Callable[[type], type]:
    """Class decorator: declare which engine State keys this Component writes.

    Stored on the class as ``__state_writes__: frozenset[str]``. Used by
    :meth:`Pipeline.validate` to detect two Components declaring writes
    on the same key — that is a build-time error because the observed
    final value would be order-dependent.
    """

    def _wrap(cls: type) -> type:
        cls.__state_writes__ = frozenset(keys)  # type: ignore[attr-defined]
        return cls

    return _wrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_component(obj: Any) -> bool:
    """Return True iff ``obj`` (class or instance) carries the @component marker."""
    return bool(getattr(obj, "__is_component__", False))


def get_input_sockets(obj: Any) -> dict[str, Socket]:
    """Return the declared input sockets for a class or instance.

    Returns an empty dict for objects that are not Components. Accepts
    either the class itself or an instance; the lookup goes through the
    usual attribute resolution.
    """
    return dict(getattr(obj, "__component_inputs__", {}))


def get_output_sockets(obj: Any) -> dict[str, Socket]:
    """Return the declared output sockets for a class or instance."""
    return dict(getattr(obj, "__component_outputs__", {}))

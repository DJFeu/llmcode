"""Tool capability Protocols (H10 — Sprint 3).

The ``Tool`` base class in :mod:`llm_code.tools.base` exposes
``is_read_only`` / ``is_destructive`` as overridable methods. That
convention works but it can't be statically checked — a subclass that
forgets to override ``is_destructive`` silently inherits a False
default. These :class:`typing.Protocol` classes give the pipeline a
``has_capability(tool, DestructiveCapability)`` check that's both
runtime-verifiable and discoverable by type-checkers.

The Protocols are ``@runtime_checkable`` so ``isinstance`` works, and
they're structural — any tool exposing the right-named methods is
counted in, which means the entire existing tool catalogue satisfies
the correct Protocol(s) without any code changes on their side.
"""
from __future__ import annotations

from typing import Any, Protocol, Type, runtime_checkable


@runtime_checkable
class ReadOnlyCapability(Protocol):
    """Tool that does not mutate filesystem / remote state."""

    def is_read_only(self, args: dict[str, Any]) -> bool: ...


@runtime_checkable
class DestructiveCapability(Protocol):
    """Tool whose invocation can mutate state (file edits, bash ``rm``,
    network POSTs to non-idempotent endpoints)."""

    def is_destructive(self, args: dict[str, Any]) -> bool: ...


@runtime_checkable
class RollbackableCapability(Protocol):
    """Destructive tool that can emit reversal operations.

    Callers obtain ``get_rollback_operations()`` *after* the tool ran
    (and before confirming the change) so the runtime can re-apply
    them on a deny / failure.
    """

    def get_rollback_operations(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class NetworkCapability(Protocol):
    """Tool that makes an outbound network call the sandbox policy
    needs to evaluate separately from filesystem access."""

    def makes_network_call(self, args: dict[str, Any]) -> bool: ...


def has_capability(tool: object, capability: Type[Any]) -> bool:
    """Return True when ``tool`` structurally satisfies ``capability``.

    Thin wrapper over ``isinstance`` — the win is readability at call
    sites (``if has_capability(tool, DestructiveCapability)`` reads
    better than ``if isinstance(tool, DestructiveCapability)`` for
    non-typing readers).
    """
    return isinstance(tool, capability)

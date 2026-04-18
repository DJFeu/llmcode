"""Sandbox backend lifecycle manager (F5 — Sprint 7).

Long-lived sandbox backends (Docker containers especially) hold
host-side state — container FDs, tmpfs mounts, exec handles — that
must be released when a session ends. Tools that call
:func:`choose_backend` repeatedly build up a collection that nobody
explicitly tracks; :class:`SandboxLifecycleManager` is the
registrar that tool pipelines and tests hand backends to so a
single ``close_all()`` can tear everything down.

Use either as a plain registry (call ``close_all`` at session end)
or as a context manager (handles the close on exit — including when
the with-block raises).
"""
from __future__ import annotations

from typing import Any


class SandboxLifecycleManager:
    """Tracks backends created during a session and closes them at end."""

    def __init__(self) -> None:
        self._backends: list[Any] = []
        self._closed: set[int] = set()

    # Use id()-keyed dedupe so two references to the same backend
    # don't get closed twice. Weakrefs would be better still, but
    # many MagicMock / dict-backed mocks can't take weakrefs — keeping
    # a plain list + id-set keeps tests simple.

    def register(self, backend: Any) -> None:
        if id(backend) in {id(b) for b in self._backends}:
            return
        self._backends.append(backend)

    @property
    def count(self) -> int:
        return len(self._backends)

    def close_all(self) -> None:
        """Close every registered backend; swallow per-backend errors."""
        for backend in self._backends:
            if id(backend) in self._closed:
                continue
            self._closed.add(id(backend))
            close_fn = getattr(backend, "close", None)
            if not callable(close_fn):
                continue
            try:
                close_fn()
            except Exception:
                pass  # teardown must not raise — caller has nothing to do

    # --- Diagnostic report --------------------------------------------

    def report(self) -> dict:
        """JSON-safe snapshot suitable for ``/diagnose`` output.

        Shape::

            {
              "registered": int,   # total backends ever handed to register()
              "closed":     int,   # how many of those close_all() visited
              "open":       int,   # registered - closed
              "backends":   [
                {"name": str, "class": str, "closed": bool},
                ...
              ],
            }

        Unnamed objects (anything without a ``name`` attribute) fall
        back to the Python class as label so the report stays
        populated for bare mocks / stubs.
        """
        entries: list[dict] = []
        for backend in self._backends:
            name = getattr(backend, "name", None)
            if not isinstance(name, str) or not name:
                name = type(backend).__name__
            entries.append({
                "name": name,
                "class": type(backend).__name__,
                "closed": id(backend) in self._closed,
            })
        return {
            "registered": len(self._backends),
            "closed": len(self._closed),
            "open": len(self._backends) - len(self._closed),
            "backends": entries,
        }

    # --- Context-manager sugar ----------------------------------------

    def __enter__(self) -> "SandboxLifecycleManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ARG002
        self.close_all()

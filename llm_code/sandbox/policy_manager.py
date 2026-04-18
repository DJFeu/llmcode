"""Sandbox policy declarative layer (H3 skeleton — Sprint 3).

The existing ``sandbox/pty_runner.py`` and ``sandbox/docker_sandbox.py``
are thin wrappers — each owns its own run semantics and there's no
policy object shared between them. This module introduces:

    * :class:`SandboxPolicy` — a platform-agnostic policy description
      (read/write/network + allow/deny path lists).
    * :class:`SandboxBackend` — a Protocol adapters can satisfy so
      ``choose_backend()`` can swap in bubblewrap (Linux), seatbelt
      (macOS), Docker, or the pty-only path without rewriting callers.
    * :func:`default_policy` / :func:`choose_backend` — entry points
      the runtime will eventually call.

Skeleton only — the existing wrappers aren't changed. Wiring this
layer into ``ToolExecutionPipeline`` lands in a follow-up so the
change stays reviewable.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxPolicy:
    """Platform-agnostic policy description.

    Path lists take precedence in the order: ``deny_paths`` always
    wins; then ``allow_paths`` (explicit allowlist); fallbacks to the
    boolean ``allow_read`` / ``allow_write`` flags. Backends enforce
    the policy using whatever primitives the OS provides.
    """
    allow_read: bool = True       # least privilege — read-only by default
    allow_write: bool = False
    allow_network: bool = False
    allow_paths: tuple[str, ...] = ()
    deny_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of a single sandboxed command."""
    exit_code: int
    stdout: str
    stderr: str

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class SandboxBackend(Protocol):
    """Adapter contract for sandbox implementations.

    Concrete adapters (pty, docker, bubblewrap, seatbelt, ...) satisfy
    this Protocol by exposing:

        * ``name``: short identifier used in logs / diagnose output
        * ``execute(command, policy)`` → :class:`SandboxResult`
    """
    name: str

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult: ...


# ── Entry points ──────────────────────────────────────────────────────


def default_policy(mode: str = "read_only") -> SandboxPolicy:
    """Return a policy preset.

    * ``read_only``   — filesystem read, no write, no network (default).
    * ``workspace``   — filesystem read+write, no network (typical
      ``edit_file`` tool scope).
    * ``full_access`` — everything including network. Usually gated
      behind an explicit user approval.
    """
    if mode == "read_only":
        return SandboxPolicy(allow_read=True, allow_write=False, allow_network=False)
    if mode == "workspace":
        return SandboxPolicy(allow_read=True, allow_write=True, allow_network=False)
    if mode == "full_access":
        return SandboxPolicy(allow_read=True, allow_write=True, allow_network=True)
    raise ValueError(f"unknown sandbox mode {mode!r}")


class _NullBackend:
    """Trivial backend that refuses to execute anything.

    Returned by :func:`choose_backend` when no concrete adapter is
    available on the host platform. Keeps the caller's code path
    total — a tool can still attempt ``backend.execute(...)`` and
    receive a well-formed failure instead of crashing.
    """
    name = "null"

    def execute(
        self, command: list[str], policy: SandboxPolicy,  # noqa: ARG002
    ) -> SandboxResult:
        return SandboxResult(
            exit_code=126,
            stdout="",
            stderr="no sandbox backend available on this host",
        )


def choose_backend() -> SandboxBackend:
    """Pick the most appropriate adapter for the current platform.

    Skeleton implementation: always returns the existing PTY wrapper
    when available, else a :class:`_NullBackend`. Concrete bwrap /
    seatbelt / docker adapters plug into this selector in follow-ups
    without breaking callers.
    """
    # For now we route everything to a null backend — the PTY adapter
    # doesn't yet implement the Protocol. Once it does, flip this.
    system = platform.system().lower()
    _ = system  # reserved for per-OS dispatch later
    return _NullBackend()

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


def choose_backend(config=None) -> SandboxBackend:
    """Pick the most appropriate adapter for the given sandbox config.

    Selection order:

        1. ``config is None`` or ``config.enabled is False``
           → :class:`_NullBackend` (legacy path keeps running).
        2. ``config.enabled is True`` and Docker/Podman available
           → :class:`DockerSandboxBackend`.
        3. ``config.enabled is True`` but Docker unavailable
           → :class:`PtySandboxBackend` (graceful fallback).

    Each call returns a fresh backend instance — no global singletons
    so parallel sessions never leak Docker container handles.
    """
    if config is None or not getattr(config, "enabled", False):
        return _NullBackend()

    # Lazy import — adapters depend on external libs (ptyprocess,
    # Docker subprocess path) that we don't want to force for
    # every import of policy_manager.
    from llm_code.sandbox.adapters import (
        DockerSandboxBackend,
        PtySandboxBackend,
    )

    try:
        docker_backend = DockerSandboxBackend(config)
        if docker_backend._sandbox.is_available():
            return docker_backend
    except Exception:
        pass  # fall through to PTY

    return PtySandboxBackend()


_ = platform  # retained for future per-OS dispatch


# ── H3 deep wire: translate SandboxConfig → SandboxPolicy ────────────

# Tool names we know are purely read-only. Resolver clamps destructive
# bits off for these even when the config otherwise allows writes, so
# a misconfigured sandbox never escalates a ``grep`` call into a
# write-capable one.
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file",
    "glob_search",
    "grep_search",
    "git_status",
    "git_diff",
    "git_log",
    "lsp_goto_definition",
    "lsp_find_references",
    "lsp_diagnostics",
    "lsp_hover",
    "lsp_document_symbol",
    "lsp_workspace_symbol",
    "memory_recall",
    "memory_list",
})


class SandboxPolicyResolver:
    """Maps the existing :class:`SandboxConfig` to a :class:`SandboxPolicy`.

    ``SandboxConfig`` describes the Docker-specific tunables
    (image, mounts, cpu/memory limits); ``SandboxPolicy`` is the
    platform-agnostic view the runtime reads before dispatching a tool.
    When the sandbox is disabled the resolver returns ``None`` so the
    legacy PTY path keeps running untouched.
    """

    def __init__(self, config) -> None:
        self._config = config

    def resolve_for_tool(
        self,
        tool_name: str,
        args: dict,  # noqa: ARG002 — reserved for per-call policy later
    ) -> SandboxPolicy | None:
        cfg = self._config
        if not getattr(cfg, "enabled", False):
            return None

        # Base policy from the SandboxConfig: mount_readonly controls
        # write, network controls network; read stays on.
        allow_write = not bool(getattr(cfg, "mount_readonly", False))
        allow_network = bool(getattr(cfg, "network", False))

        # Clamp to least privilege for known read-only tools.
        if tool_name in READ_ONLY_TOOL_NAMES:
            allow_write = False

        return SandboxPolicy(
            allow_read=True,
            allow_write=allow_write,
            allow_network=allow_network,
        )

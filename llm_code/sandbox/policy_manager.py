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


@runtime_checkable
class StreamingSandboxBackend(SandboxBackend, Protocol):
    """Optional extension for backends that can emit output chunk-by-chunk.

    Long-running bash invocations (builds, tests, deploys) need to
    surface output as it arrives; the synchronous ``execute`` path
    blocks the whole command before returning anything.

    Adapters that implement this Protocol provide
    ``execute_streaming(command, policy, *, on_chunk)`` which invokes
    ``on_chunk(text)`` for every output chunk and returns the final
    :class:`SandboxResult` once the command exits. Callers detect
    support via :func:`has_streaming`.
    """

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk,  # Callable[[str], None]
    ) -> SandboxResult: ...


def has_streaming(backend) -> bool:
    """Return True when ``backend.execute_streaming`` is callable.

    Protocol isinstance on :class:`StreamingSandboxBackend` inherits
    the data-attribute quirk (``name: str``) so Python 3.12 refuses
    the check. Callers should use this helper instead.
    """
    fn = getattr(backend, "execute_streaming", None)
    return callable(fn)


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


def _detect_platform() -> str:
    """Return the effective platform string with WSL detection (F3).

    WSL2 reports ``Linux`` via ``platform.system()`` on most builds so
    the normal Linux chain picks up. We still check
    ``/proc/sys/kernel/osrelease`` for ``microsoft`` / ``WSL`` so rare
    hosts where CPython misreports Windows get upgraded to Linux and
    route through Landlock / Bwrap instead of the Docker-or-null
    Windows chain.
    """
    system = platform.system()
    try:
        with open("/proc/sys/kernel/osrelease") as fh:
            release = fh.read().strip().lower()
        if "microsoft" in release or "wsl" in release:
            return "Linux"
    except (OSError, FileNotFoundError):
        pass
    return system


def choose_backend(config=None) -> SandboxBackend:
    """Pick the most appropriate sandbox adapter for this host.

    Selection order (A1 / F3 — platform-aware + WSL detection):

        1. ``config is None`` or ``config.enabled is False``
           → :class:`_NullBackend`. Legacy code path stays untouched.
        2. Linux (including WSL2) → Landlock → Bwrap → Docker → PTY.
        3. Darwin → Seatbelt → Docker → PTY.
        4. Windows (bare) → Docker → _NullBackend (PTY not available).
        5. Any other platform → _NullBackend.

    Each call returns a fresh backend instance — no global singletons
    so parallel sessions never share Docker container handles. Each
    constructor failure degrades quietly to the next candidate.
    """
    if config is None or not getattr(config, "enabled", False):
        return _NullBackend()

    system = _detect_platform()
    if system == "Linux":
        return _linux_priority(config)
    if system == "Darwin":
        return _darwin_priority(config)
    if system == "Windows":
        return _windows_priority(config)
    return _NullBackend()


def _try(factory, *args, **kwargs):
    """Construct ``factory`` and swallow any init error.

    Returns the constructed backend or ``None`` so the priority chain
    can fall through on unavailable platforms / missing binaries.
    """
    try:
        return factory(*args, **kwargs)
    except Exception:
        return None


def _linux_priority(config) -> SandboxBackend:
    from llm_code.sandbox.adapters import DockerSandboxBackend, PtySandboxBackend
    from llm_code.sandbox.bwrap import BwrapSandboxBackend
    from llm_code.sandbox.landlock import LandlockSandboxBackend

    for factory in (LandlockSandboxBackend, BwrapSandboxBackend):
        backend = _try(factory)
        if backend is not None:
            return backend
    docker = _try(DockerSandboxBackend, config)
    if docker is not None and docker._sandbox.is_available():
        return docker
    return PtySandboxBackend()


def _darwin_priority(config) -> SandboxBackend:
    from llm_code.sandbox.adapters import DockerSandboxBackend, PtySandboxBackend
    from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend

    seatbelt = _try(SeatbeltSandboxBackend)
    if seatbelt is not None:
        return seatbelt
    docker = _try(DockerSandboxBackend, config)
    if docker is not None and docker._sandbox.is_available():
        return docker
    return PtySandboxBackend()


def _windows_priority(config) -> SandboxBackend:
    from llm_code.sandbox.adapters import DockerSandboxBackend

    docker = _try(DockerSandboxBackend, config)
    if docker is not None and docker._sandbox.is_available():
        return docker
    return _NullBackend()


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

"""Concrete :class:`SandboxBackend` adapters (S4.1).

Wraps the two existing execution primitives (:func:`run_pty` and
:class:`DockerSandbox`) behind the policy_manager Protocol so
:func:`choose_backend` can return something that actually runs.
Neither adapter enforces the :class:`SandboxPolicy` at the OS level —
that's a concrete bwrap / landlock / seatbelt backend's job; the
adapters here at least surface the policy to the backend so the
Docker adapter can wire ``--network=none`` once it grows that.
"""
from __future__ import annotations

import shlex
import time
from typing import Callable

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult
from llm_code.tools.sandbox import DockerSandbox, SandboxConfig, run_pty


def _import_pty_process():
    """Late-import ``ptyprocess.PtyProcessUnicode`` so tests can
    monkeypatch this hook without disturbing module load order."""
    from ptyprocess import PtyProcessUnicode
    return PtyProcessUnicode


# Ambient reference so tests can patch ``adapters.PtyProcessUnicode``
# directly as well as via ``_import_pty_process``. Lives as None until
# first streaming call; callers shouldn't rely on its presence.
PtyProcessUnicode = None  # type: ignore[assignment]


class PtySandboxBackend:
    """Run commands through the PTY wrapper (no OS-level sandbox)."""

    name = "pty"

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,  # noqa: ARG002 — policy not yet enforced at PTY
    ) -> SandboxResult:
        """Run ``command`` via :func:`run_pty` and translate the result."""
        cmd_str = " ".join(shlex.quote(part) for part in command)
        try:
            pty_result = run_pty(cmd_str, timeout=self._timeout)
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"pty backend error: {exc}",
            )
        stderr = ""
        if getattr(pty_result, "timed_out", False):
            stderr = f"timed out after {self._timeout}s"
        return SandboxResult(
            exit_code=pty_result.returncode,
            stdout=pty_result.output,
            stderr=stderr,
        )

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,  # noqa: ARG002 — PTY can't enforce policy itself
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        """E3 — stream output chunks via ``on_chunk``.

        Spawns a PTY, reads in 4KB chunks until the child exits,
        invokes ``on_chunk(text)`` for every non-empty chunk, then
        returns a :class:`SandboxResult` with the concatenated output
        + exit code. Callback exceptions are swallowed so a buggy UI
        cannot wedge execution.
        """
        cmd_str = " ".join(shlex.quote(part) for part in command)

        # Prefer the module-level attribute so tests can patch it
        # directly; fall back to the import helper in production.
        pty_cls = globals().get("PtyProcessUnicode")
        if pty_cls is None:
            try:
                pty_cls = _import_pty_process()
            except ImportError as exc:
                return SandboxResult(
                    exit_code=1, stdout="",
                    stderr=f"ptyprocess not installed: {exc}",
                )

        try:
            proc = pty_cls.spawn(["sh", "-c", cmd_str])
        except Exception as exc:
            return SandboxResult(
                exit_code=1, stdout="",
                stderr=f"pty spawn failed: {exc}",
            )

        deadline = time.monotonic() + self._timeout
        chunks: list[str] = []
        timed_out = False
        while True:
            if not proc.isalive():
                break
            if time.monotonic() > deadline:
                try:
                    proc.terminate(force=True)
                except Exception:
                    pass
                timed_out = True
                break
            try:
                chunk = proc.read(4096)
            except EOFError:
                break
            except Exception:
                break
            if not chunk:
                continue
            chunks.append(chunk)
            try:
                on_chunk(chunk)
            except Exception:
                pass  # caller bug must not wedge exec

        exit_code = getattr(proc, "exitstatus", None)
        if exit_code is None:
            exit_code = 124 if timed_out else 0
        return SandboxResult(
            exit_code=124 if timed_out else exit_code,
            stdout="".join(chunks),
            stderr=f"timed out after {self._timeout}s" if timed_out else "",
        )


class DockerSandboxBackend:
    """Run commands through the existing Docker sandbox.

    The adapter holds a long-lived :class:`DockerSandbox` instance so
    repeated executes reuse the same container. Call :meth:`close`
    when done (the runtime does this at session end).
    """

    name = "docker"

    def __init__(self, config: SandboxConfig) -> None:
        # Let constructor errors propagate — the caller decides whether
        # to fall back to PTY. ``choose_backend`` handles that dispatch.
        self._config = config
        self._sandbox = DockerSandbox(config)

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        # M2: per-call policy enforcement. Docker's --network=none /
        # --read-only are launch-time flags, so a hot-path retighten
        # would require restarting the container. Instead, reject the
        # call when the policy is stricter than the container's launch
        # config — caller can either re-launch with the right config
        # or route to a different backend.
        cfg = self._config
        if cfg is not None:
            container_network = bool(getattr(cfg, "network", True))
            container_writable = not bool(getattr(cfg, "mount_readonly", False))
            if container_network and not policy.allow_network:
                return SandboxResult(
                    exit_code=126,
                    stdout="",
                    stderr=(
                        "policy rejected: call requires network=False but "
                        "docker container was launched with network=True"
                    ),
                )
            if container_writable and not policy.allow_write:
                return SandboxResult(
                    exit_code=126,
                    stdout="",
                    stderr=(
                        "policy rejected: call requires allow_write=False "
                        "(read-only) but docker container was launched "
                        "with a writable mount"
                    ),
                )

        try:
            raw = self._sandbox.run(command)
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"docker backend error: {exc}",
            )
        stderr = getattr(raw, "stderr", "") or ""
        if getattr(raw, "timed_out", False):
            stderr = f"{stderr}\n(docker sandbox: execution timed out)".strip()
        return SandboxResult(
            exit_code=getattr(raw, "returncode", 1),
            stdout=getattr(raw, "stdout", ""),
            stderr=stderr,
        )

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        """E4 — *degraded* streaming for Docker.

        The existing :class:`DockerSandbox.run` is blocking, so true
        per-line streaming would require a ``docker exec -i`` Popen
        rewrite beyond the scope of this sprint. The degraded path
        runs :meth:`execute` normally, then emits the full stdout as
        a single chunk. Caller code that polls on_chunk for progress
        still sees output once, just not incrementally.

        The M2 policy-enforcement gate applies before any chunk is
        emitted; a rejected call produces zero chunks and exit 126,
        matching the non-streaming behaviour.
        """
        result = self.execute(command, policy)
        if result.stdout:
            try:
                on_chunk(result.stdout)
            except Exception:
                pass
        return result

    def close(self) -> None:
        close_fn = getattr(self._sandbox, "stop", None) or getattr(
            self._sandbox, "close", None
        )
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass

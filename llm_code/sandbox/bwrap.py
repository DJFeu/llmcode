"""Linux bubblewrap sandbox backend (E1 — Sprint 5).

`bwrap` is a Linux SUID helper (part of the Flatpak toolchain) that
runs a command inside fresh user / mount / net / pid namespaces. Unlike
Docker's long-lived container model, bwrap spawns a new sandbox for
each invocation, so ``SandboxPolicy`` lands directly in the argv of
every call — no launch-time-vs-call-time gap.

This backend translates :class:`SandboxPolicy` into bwrap flags:

    * ``allow_network=False`` → ``--unshare-net``
      (``--share-net`` otherwise so the host network is joined.)
    * ``allow_write=False``   → workspace mounted ``--ro-bind``
    * ``allow_write=True``    → workspace mounted ``--bind``
    * Fresh ``/tmp`` always gets a ``--tmpfs``
    * ``/proc`` / ``/sys`` remounted as read-only namespaces

Requires the ``bwrap`` binary on the host. The constructor raises
RuntimeError when it's missing so :func:`choose_backend` can fall
back cleanly.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult


_DEFAULT_TIMEOUT_SECONDS = 30


class BwrapSandboxBackend:
    """Linux bubblewrap per-call sandbox."""

    name = "bwrap"

    def __init__(
        self,
        *,
        workspace: str | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise RuntimeError(
                "bwrap not found on PATH — install bubblewrap or route "
                "SandboxBackend selection to a different backend."
            )
        self._bwrap = bwrap
        self._workspace = str(Path(workspace).resolve()) if workspace else str(Path.cwd())
        self._timeout = timeout_seconds

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        args = self._build_argv(command, policy)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124,
                stdout="",
                stderr=f"bwrap call timed out after {self._timeout}s",
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"bwrap backend error: {exc}",
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        """E4 — real per-line streaming under bwrap.

        Spawns bwrap under ``subprocess.Popen`` with merged stdout+stderr
        (bwrap only writes its own errors to stderr; child output goes
        to stdout by default when we ask for line-buffered text mode),
        emits each line through ``on_chunk``, then waits for exit.
        Callback exceptions are swallowed.
        """
        argv = self._build_argv(command, policy)
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"bwrap spawn failed: {exc}",
            )

        chunks: list[str] = []
        stdout_stream = proc.stdout or iter(())
        for line in stdout_stream:
            chunks.append(line)
            try:
                on_chunk(line)
            except Exception:
                pass

        try:
            exit_code = proc.wait(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            return SandboxResult(
                exit_code=124,
                stdout="".join(chunks),
                stderr=f"bwrap streaming timed out after {self._timeout}s",
            )

        return SandboxResult(
            exit_code=exit_code,
            stdout="".join(chunks),
            stderr="",
        )

    # ------------------------------------------------------------------

    def _build_argv(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> list[str]:
        argv: list[str] = [self._bwrap]

        # Network namespace
        if policy.allow_network:
            argv.append("--share-net")
        else:
            argv.append("--unshare-net")

        # Always isolate pid / uts / ipc / cgroup so leaked FDs don't
        # escape sandbox boundaries.
        argv.extend([
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--die-with-parent",
        ])

        # Mount host filesystem.
        # Least privilege: bind-mount system dirs read-only; workspace
        # is writable or read-only based on policy.
        bind_flag = "--bind" if policy.allow_write else "--ro-bind"
        argv.extend([bind_flag, self._workspace, self._workspace])
        # Read-only rest of the filesystem so tools can resolve ``sh``,
        # libraries, etc., without giving them write access.
        for src in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"):
            if Path(src).exists():
                argv.extend(["--ro-bind", src, src])

        # Fresh /tmp + /proc + /sys so the sandbox doesn't share them
        # with the host.
        argv.extend([
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
        ])

        # Working directory — run inside the workspace so relative
        # paths in the command line resolve as the caller expects.
        argv.extend(["--chdir", self._workspace])

        # The command itself. bwrap treats everything after its own
        # flags as the program to exec; no ``--`` separator needed
        # but we leave it implicit.
        argv.extend(command)
        return argv

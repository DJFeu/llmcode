"""Linux landlock LSM sandbox backend (L1 + F1).

Tries real landlock enforcement via the ctypes implementation in
:mod:`llm_code.sandbox.landlock_ctypes` (syscalls 444-446 +
``prctl(PR_SET_NO_NEW_PRIVS)``). Falls back to
:class:`BwrapSandboxBackend` when the ctypes path is unavailable
(missing libc, old kernel, non-x86_64). The public API is stable
either way so callers don't care which internal path ran.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult


_KERNEL_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)")


def _kernel_at_least(major: int, minor: int) -> bool:
    """Return True if ``uname.release`` advertises kernel >= major.minor."""
    try:
        release = os.uname().release
    except Exception:
        return False
    m = _KERNEL_RELEASE_RE.match(release)
    if not m:
        return False
    host_major, host_minor = int(m.group(1)), int(m.group(2))
    return (host_major, host_minor) >= (major, minor)


class LandlockSandboxBackend:
    """Linux landlock sandbox backend (skeleton + ctypes integration).

    Priorities:

        1. ctypes path — :func:`apply_landlock` called in a Popen
           ``preexec_fn`` so the kernel enforces the policy before
           ``execve``.
        2. bwrap delegation — when ctypes cannot drive landlock on
           this host, fall back to bubblewrap (which itself uses
           landlock on new-enough kernels).
    """

    name = "landlock"

    def __init__(
        self,
        *,
        workspace: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        sysname = ""
        try:
            sysname = os.uname().sysname
        except Exception:
            sysname = ""
        if sysname.lower() != "linux":
            raise RuntimeError(
                f"LandlockSandboxBackend requires Linux (got sysname={sysname!r})"
            )
        if not _kernel_at_least(5, 13):
            raise RuntimeError(
                "LandlockSandboxBackend requires kernel >= 5.13 (landlock LSM)"
            )

        from llm_code.sandbox.landlock_ctypes import is_landlock_available

        self._workspace = str(Path(workspace).resolve()) if workspace else str(Path.cwd())
        self._timeout = timeout_seconds
        self._ctypes_path = False
        self._delegate = None

        if is_landlock_available():
            self._ctypes_path = True
            return

        if not shutil.which("bwrap"):
            raise RuntimeError(
                "LandlockSandboxBackend requires either (1) ctypes support "
                "for direct landlock syscalls, or (2) bubblewrap on PATH "
                "as a delegation fallback."
            )

        from llm_code.sandbox.bwrap import BwrapSandboxBackend
        self._delegate = BwrapSandboxBackend(
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        if self._ctypes_path:
            return self._execute_via_ctypes(command, policy)
        return self._delegate.execute(command, policy)

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        if self._ctypes_path:
            return self._execute_streaming_via_ctypes(
                command, policy, on_chunk=on_chunk,
            )
        return self._delegate.execute_streaming(
            command, policy, on_chunk=on_chunk,
        )

    # ------------------------------------------------------------------

    def _make_preexec(self, policy: SandboxPolicy):
        workspace = self._workspace

        def _preexec():
            from llm_code.sandbox.landlock_ctypes import apply_landlock
            apply_landlock(policy, workspace)

        return _preexec

    def _execute_via_ctypes(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        import shlex
        import subprocess

        cmd_str = " ".join(shlex.quote(p) for p in command)
        try:
            proc = subprocess.run(
                ["sh", "-c", cmd_str],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                preexec_fn=self._make_preexec(policy),
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124, stdout="",
                stderr=f"landlock exec timed out after {self._timeout}s",
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1, stdout="",
                stderr=f"landlock backend error: {exc}",
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def _execute_streaming_via_ctypes(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        import shlex
        import subprocess

        cmd_str = " ".join(shlex.quote(p) for p in command)
        try:
            proc = subprocess.Popen(
                ["sh", "-c", cmd_str],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=self._make_preexec(policy),
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1, stdout="",
                stderr=f"landlock spawn failed: {exc}",
            )
        chunks: list[str] = []
        stream = proc.stdout or iter(())
        for line in stream:
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
                stderr=f"landlock streaming timed out after {self._timeout}s",
            )
        return SandboxResult(
            exit_code=exit_code,
            stdout="".join(chunks),
            stderr="",
        )

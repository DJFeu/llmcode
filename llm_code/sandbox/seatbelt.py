"""macOS sandbox-exec backend (E2 — Sprint 5).

``sandbox-exec`` is Apple's seatbelt sandbox CLI. It runs a command
under a TinyScheme-like profile that lists what's allowed; anything
not allowed is denied (when ``deny default`` is set). Apple marks
the binary deprecated but it remains the only user-space sandbox on
stock macOS, so it's still the right default for ``darwin`` hosts.

Profile shape per call (dynamically assembled from SandboxPolicy):

    (version 1)
    (deny default)
    (allow process-exec)
    (allow process-fork)
    (allow signal (target self))
    (allow sysctl-read)
    (allow file-read*)            ; when allow_read
    (allow file-write* (regex ...)) ; when allow_write, scoped to workspace
    (allow network*)              ; when allow_network

The profile is passed via ``-p`` so we don't write a tempfile per
invocation — cleans up nicely and avoids inode churn in hot paths.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult


_DEFAULT_TIMEOUT_SECONDS = 30


class SeatbeltSandboxBackend:
    """macOS sandbox-exec per-call sandbox."""

    name = "seatbelt"

    def __init__(
        self,
        *,
        workspace: str | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        bin_path = shutil.which("sandbox-exec")
        if not bin_path:
            raise RuntimeError(
                "sandbox-exec not found on PATH — not a macOS host, or "
                "the deprecated seatbelt binary has been removed."
            )
        self._bin = bin_path
        self._workspace = (
            str(Path(workspace).resolve()) if workspace else str(Path.cwd())
        )
        self._timeout = timeout_seconds

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        profile = self._render_profile(policy)
        argv = [self._bin, "-p", profile, *command]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                exit_code=124,
                stdout="",
                stderr=f"sandbox-exec timed out after {self._timeout}s",
            )
        except Exception as exc:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=f"sandbox-exec backend error: {exc}",
            )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    # ------------------------------------------------------------------

    def _render_profile(self, policy: SandboxPolicy) -> str:
        lines = [
            "(version 1)",
            "(deny default)",
            # Process management — the command itself needs these to
            # exec. Signals scoped to self so the child can't send to
            # other processes.
            "(allow process-exec)",
            "(allow process-fork)",
            "(allow signal (target self))",
            "(allow sysctl-read)",
            # Mach / IPC basics so standard libc calls resolve.
            "(allow mach-lookup)",
        ]
        if policy.allow_read:
            lines.append("(allow file-read*)")
        if policy.allow_write:
            # Scope writes to the workspace subtree so even a writable
            # sandbox can't rewrite /System or the user's home.
            lines.append(
                f'(allow file-write* (subpath "{self._workspace}"))'
            )
            lines.append('(allow file-write* (subpath "/tmp"))')
            lines.append('(allow file-write* (subpath "/private/tmp"))')
            lines.append('(allow file-write* (subpath "/private/var/folders"))')
        if policy.allow_network:
            lines.append("(allow network*)")
        return "\n".join(lines) + "\n"

"""Sandboxed execution backends for the bash tool.

Phase 5.4 of the 2026-04-11 architecture refactor: this module consolidates
the two small sandbox backends (``DockerSandbox`` and ``run_pty``) that
previously lived under ``llm_code/sandbox/`` into a single tools-layer
module. The old package is kept as a thin backward-compatibility shim so
existing imports (``from llm_code.sandbox import DockerSandbox``) keep
working.

Two independent execution backends live here:

* :class:`DockerSandbox` — wraps ``docker exec`` / ``podman exec`` so the
  bash tool can run untrusted commands inside a container with the
  workspace mounted but no access to the host filesystem or credentials.
* :func:`run_pty` — spawns commands in a real pseudo-terminal via
  ``ptyprocess`` so programs requiring a TTY (``git rebase -i``,
  ``python -i``, curses tools) work correctly. Output is rendered through
  a ``pyte`` virtual terminal when available, falling back to ANSI-stripped
  raw text.

They share no state — the bash tool picks whichever applies to the
command at hand.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


# ── Docker sandbox ──────────────────────────────────────────────────────

_DEFAULT_IMAGE = "python:3.13-slim"
_CONTAINER_PREFIX = "llmcode-sandbox"


@dataclass(frozen=True)
class SandboxConfig:
    """Configuration for Docker sandbox execution."""

    enabled: bool = False
    image: str = _DEFAULT_IMAGE
    runtime: str = ""  # "docker" | "podman" | "" (auto-detect)
    network: bool = True  # allow network access
    mount_readonly: bool = False  # mount workspace as read-only
    extra_mounts: tuple[str, ...] = ()  # additional -v mounts
    extra_args: tuple[str, ...] = ()  # additional docker run args
    memory_limit: str = "2g"  # container memory limit
    cpu_limit: str = "2"  # container CPU limit


@dataclass
class SandboxResult:
    """Result from a sandboxed command execution."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


class DockerSandbox:
    """Manages a long-lived container for sandboxed command execution."""

    def __init__(
        self,
        config: SandboxConfig,
        workspace: Path | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace or Path.cwd()
        self._runtime_cmd = self._detect_runtime()
        self._container_id: str | None = None

    def _detect_runtime(self) -> str:
        """Detect docker or podman."""
        if self._config.runtime:
            return self._config.runtime
        if shutil.which("docker"):
            return "docker"
        if shutil.which("podman"):
            return "podman"
        return ""

    def is_available(self) -> bool:
        """Check if container runtime is available and sandbox is enabled."""
        if not self._config.enabled:
            return False
        if not self._runtime_cmd:
            _log.debug("sandbox enabled but no docker/podman found")
            return False
        # Quick check: can we run the runtime?
        try:
            proc = subprocess.run(
                [self._runtime_cmd, "info"],
                capture_output=True, timeout=5,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def ensure_running(self) -> bool:
        """Start the sandbox container if not already running.

        Returns True if the container is running after this call.
        """
        if self._container_id:
            # Check if still alive
            try:
                proc = subprocess.run(
                    [self._runtime_cmd, "inspect", "-f", "{{.State.Running}}", self._container_id],
                    capture_output=True, text=True, timeout=5,
                )
                if proc.stdout.strip() == "true":
                    return True
            except Exception:
                pass
            self._container_id = None

        # Start a new container
        container_name = f"{_CONTAINER_PREFIX}-{id(self) % 10000}"
        mount_flag = "ro" if self._config.mount_readonly else "rw"
        cmd = [
            self._runtime_cmd, "run", "-d",
            "--name", container_name,
            "-v", f"{self._workspace}:/workspace:{mount_flag}",
            "-w", "/workspace",
            "--memory", self._config.memory_limit,
            "--cpus", self._config.cpu_limit,
        ]

        if not self._config.network:
            cmd.extend(["--network", "none"])

        for mount in self._config.extra_mounts:
            cmd.extend(["-v", mount])

        for arg in self._config.extra_args:
            cmd.append(arg)

        # Keep container alive with a sleep process
        cmd.extend([self._config.image, "sleep", "infinity"])

        try:
            # Clean up any existing container with same name
            subprocess.run(
                [self._runtime_cmd, "rm", "-f", container_name],
                capture_output=True, timeout=10,
            )
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                _log.warning("sandbox container start failed: %s", proc.stderr[:300])
                return False
            self._container_id = proc.stdout.strip()[:12]
            _log.info("sandbox container started: %s (image=%s)", self._container_id, self._config.image)
            return True
        except subprocess.TimeoutExpired:
            _log.warning("sandbox container start timed out")
            return False
        except Exception as exc:
            _log.warning("sandbox container start error: %s", exc)
            return False

    def run(
        self,
        command: str,
        *,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> SandboxResult:
        """Execute a command inside the sandbox container.

        The container must already be running (call ``ensure_running()`` first).
        """
        if not self._container_id:
            return SandboxResult(
                stdout="", stderr="sandbox container not running",
                returncode=1,
            )

        exec_cmd = [self._runtime_cmd, "exec"]
        if cwd:
            exec_cmd.extend(["-w", cwd])
        exec_cmd.extend([self._container_id, "sh", "-c", command])

        try:
            proc = subprocess.run(
                exec_cmd,
                capture_output=True, text=True,
                timeout=timeout,
            )
            return SandboxResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                stdout="", stderr=f"Command timed out after {timeout}s",
                returncode=124, timed_out=True,
            )
        except Exception as exc:
            return SandboxResult(
                stdout="", stderr=str(exc),
                returncode=1,
            )

    def cleanup(self) -> None:
        """Stop and remove the sandbox container."""
        if not self._container_id:
            return
        try:
            subprocess.run(
                [self._runtime_cmd, "rm", "-f", self._container_id],
                capture_output=True, timeout=10,
            )
            _log.info("sandbox container removed: %s", self._container_id)
        except Exception:
            pass
        self._container_id = None


# ── PTY runner ──────────────────────────────────────────────────────────


@dataclass
class PTYResult:
    """Result from a PTY command execution."""

    output: str
    returncode: int
    timed_out: bool = False


def run_pty(
    command: str,
    *,
    timeout: int = 30,
    cols: int = 120,
    rows: int = 40,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> PTYResult:
    """Run a command in a PTY and capture its output.

    Uses ``ptyprocess.PtyProcessUnicode`` for the pseudo-terminal
    and optionally ``pyte`` for screen rendering. If the command
    completes before ``timeout``, the output is returned immediately.
    """
    try:
        from ptyprocess import PtyProcessUnicode
    except ImportError:
        return PTYResult(
            output="ptyprocess not installed — PTY mode unavailable",
            returncode=1,
        )

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    merged_env["TERM"] = "xterm-256color"
    merged_env["COLUMNS"] = str(cols)
    merged_env["LINES"] = str(rows)

    try:
        proc = PtyProcessUnicode.spawn(
            ["sh", "-c", command],
            dimensions=(rows, cols),
            env=merged_env,
            cwd=cwd,
        )
    except Exception as exc:
        return PTYResult(output=f"PTY spawn failed: {exc}", returncode=1)

    # Collect output with timeout
    output_chunks: list[str] = []
    deadline = time.monotonic() + timeout

    try:
        while proc.isalive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.terminate(force=True)
                return PTYResult(
                    output="".join(output_chunks),
                    returncode=124,
                    timed_out=True,
                )
            try:
                chunk = proc.read(4096)
                if chunk:
                    output_chunks.append(chunk)
            except EOFError:
                break
            except Exception:
                break
    finally:
        if proc.isalive():
            proc.terminate(force=True)

    # Try to render through pyte for clean screen output
    raw_output = "".join(output_chunks)
    rendered = _render_with_pyte(raw_output, cols, rows)

    return PTYResult(
        output=rendered,
        returncode=proc.exitstatus or 0,
    )


def _render_with_pyte(raw: str, cols: int, rows: int) -> str:
    """Render raw terminal output through pyte to get clean text.

    Falls back to raw output (with ANSI stripped) if pyte is unavailable.
    """
    try:
        import pyte
        screen = pyte.Screen(cols, rows)
        stream = pyte.Stream(screen)
        stream.feed(raw)
        # Extract non-empty lines from the screen
        lines = []
        for row in range(rows):
            line = screen.display[row].rstrip()
            lines.append(line)
        # Trim trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines) if lines else raw
    except ImportError:
        # Strip ANSI escape codes as best-effort
        import re
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw)
    except Exception:
        return raw


__all__ = [
    "DockerSandbox",
    "PTYResult",
    "SandboxConfig",
    "SandboxResult",
    "run_pty",
]

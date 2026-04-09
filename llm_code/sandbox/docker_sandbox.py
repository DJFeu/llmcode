"""Docker/Podman sandbox for isolated shell command execution.

Wraps ``docker exec`` (or ``podman exec``) so the bash tool can run
untrusted commands inside a container with the workspace mounted
read-write but no access to the host filesystem, network credentials,
or other sensitive resources.

Usage::

    sandbox = DockerSandbox(SandboxConfig(enabled=True))
    if sandbox.is_available():
        result = sandbox.run("ls -la", timeout=30, cwd="/workspace")
    else:
        # fall back to host execution
        ...

The container lifecycle is managed per-session: ``ensure_running()``
starts a long-lived container on first use, subsequent ``run()`` calls
exec inside it. ``cleanup()`` stops and removes the container.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

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

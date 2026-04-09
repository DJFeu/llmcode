"""Sandbox execution backends for shell commands.

Provides Docker/Podman container isolation so the bash tool can
execute untrusted commands without affecting the host system.
"""
from llm_code.sandbox.docker_sandbox import DockerSandbox, SandboxConfig

__all__ = ["DockerSandbox", "SandboxConfig"]

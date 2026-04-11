"""Backward-compatibility shim.

The Docker sandbox lives at :mod:`llm_code.tools.sandbox` now. This module
re-exports the public names so ``from llm_code.sandbox.docker_sandbox
import DockerSandbox`` keeps working after the Phase 5.4 merge.
"""
from llm_code.tools.sandbox import (  # noqa: F401
    DockerSandbox,
    SandboxConfig,
    SandboxResult,
)

__all__ = ["DockerSandbox", "SandboxConfig", "SandboxResult"]

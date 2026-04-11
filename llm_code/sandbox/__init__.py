"""Sandbox execution backends — backward-compatibility re-exports.

The canonical module is now ``llm_code.tools.sandbox`` (Phase 5.4 of the
2026-04-11 architecture refactor). This package survives only as a thin
re-export layer so legacy imports like ``from llm_code.sandbox import
DockerSandbox`` keep working.
"""
from llm_code.tools.sandbox import (  # noqa: F401
    DockerSandbox,
    PTYResult,
    SandboxConfig,
    SandboxResult,
    run_pty,
)

__all__ = [
    "DockerSandbox",
    "PTYResult",
    "SandboxConfig",
    "SandboxResult",
    "run_pty",
]

"""Backward-compatibility shim.

The PTY runner lives at :mod:`llm_code.tools.sandbox` now. This module
re-exports ``run_pty`` and ``PTYResult`` so legacy imports keep working
after the Phase 5.4 merge.
"""
from llm_code.tools.sandbox import PTYResult, run_pty  # noqa: F401

__all__ = ["PTYResult", "run_pty"]

"""Smoke test: AgentTool registration in AppState.from_config uses subagent_factory, not None.

Pre-M10.3 the registration block lived in ``tui/runtime_init.py``.
After M10.3 the subsystem-assembly body moved to
``runtime/app_state.py``; the tests follow it. The regression these
guards protect against — passing ``runtime_factory=None`` and
breaking subagent spawning — is unchanged.
"""
from __future__ import annotations

import inspect

import llm_code.runtime.app_state as app_state


def test_app_state_imports_subagent_factory() -> None:
    src = inspect.getsource(app_state)
    assert "subagent_factory" in src
    assert "make_subagent_runtime" in src


def test_app_state_does_not_pass_runtime_factory_none() -> None:
    """Make sure the broken `runtime_factory=None` literal is gone."""
    src = inspect.getsource(app_state)
    assert "runtime_factory=None" not in src

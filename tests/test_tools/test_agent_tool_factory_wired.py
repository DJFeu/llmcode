"""Smoke test: AgentTool registration in tui.app uses subagent_factory, not None."""
from __future__ import annotations

import inspect

import llm_code.tui.runtime_init as runtime_init


def test_tui_imports_subagent_factory() -> None:
    src = inspect.getsource(runtime_init)
    assert "subagent_factory" in src
    assert "make_subagent_runtime" in src


def test_tui_does_not_pass_runtime_factory_none() -> None:
    """Make sure the broken `runtime_factory=None` literal is gone."""
    src = inspect.getsource(runtime_init)
    assert "runtime_factory=None" not in src

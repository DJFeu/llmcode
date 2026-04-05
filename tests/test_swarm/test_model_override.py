"""Tests for SwarmManager._resolve_model — 4-level fallback chain."""
from __future__ import annotations

import pytest

from llm_code.runtime.config import (
    ModelRoutingConfig,
    RuntimeConfig,
    SwarmConfig,
)
from llm_code.swarm.manager import SwarmManager


def _make_manager(
    global_model: str = "global-model",
    sub_agent: str = "",
    role_models: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
    tmp_path_factory=None,
    swarm_dir=None,
) -> SwarmManager:
    """Helper to build a SwarmManager with specific config."""
    cfg = RuntimeConfig(
        model=global_model,
        model_routing=ModelRoutingConfig(sub_agent=sub_agent),
        swarm=SwarmConfig(role_models=role_models or {}),
        model_aliases=aliases or {},
    )
    return SwarmManager(swarm_dir=swarm_dir, config=cfg)


@pytest.fixture()
def tmp_swarm_dir(tmp_path):
    return tmp_path / "swarm"


# ── explicit model wins ────────────────────────────────────────────────────────

def test_explicit_model_wins(tmp_swarm_dir):
    """Explicit model argument beats all other levels."""
    manager = _make_manager(
        global_model="global",
        sub_agent="sub-agent-model",
        role_models={"tester": "role-model"},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("tester", explicit="explicit-model")
    assert result == "explicit-model"


# ── role mapping ────────────────────────────────────────────────────────────────

def test_role_mapping(tmp_swarm_dir):
    """role_models lookup wins when no explicit model provided."""
    manager = _make_manager(
        global_model="global",
        role_models={"reviewer": "qwen-large"},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("reviewer", explicit=None)
    assert result == "qwen-large"


def test_role_mapping_with_alias_resolution(tmp_swarm_dir):
    """role_models value is resolved through model_aliases."""
    manager = _make_manager(
        global_model="global",
        role_models={"reviewer": "large"},
        aliases={"large": "qwen-72b-instruct"},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("reviewer", explicit=None)
    assert result == "qwen-72b-instruct"


# ── fallback to model_routing.sub_agent ────────────────────────────────────────

def test_fallback_to_sub_agent(tmp_swarm_dir):
    """Falls back to model_routing.sub_agent when role not in role_models."""
    manager = _make_manager(
        global_model="global",
        sub_agent="sub-agent-model",
        role_models={},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("unknown-role", explicit=None)
    assert result == "sub-agent-model"


# ── fallback to global model ───────────────────────────────────────────────────

def test_fallback_to_global_model(tmp_swarm_dir):
    """Falls back to global RuntimeConfig.model when all else is empty."""
    manager = _make_manager(
        global_model="global-model",
        sub_agent="",
        role_models={},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("unknown-role", explicit=None)
    assert result == "global-model"


# ── explicit alias resolved ────────────────────────────────────────────────────

def test_explicit_alias_resolved(tmp_swarm_dir):
    """Explicit model name is resolved through model_aliases."""
    manager = _make_manager(
        aliases={"fast": "qwen-7b-chat"},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("any-role", explicit="fast")
    assert result == "qwen-7b-chat"


# ── role not found, sub_agent empty → global ──────────────────────────────────

def test_role_not_found_sub_agent_empty_uses_global(tmp_swarm_dir):
    """When role not found and sub_agent empty, returns global model."""
    manager = _make_manager(
        global_model="my-global",
        sub_agent="",
        role_models={"other-role": "other-model"},
        swarm_dir=tmp_swarm_dir,
    )
    result = manager._resolve_model("missing-role", explicit=None)
    assert result == "my-global"

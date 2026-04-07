"""Integration test: load_config invokes pending migrations before validation.

(There is no separate ``HardenedConfig`` class in this codebase — ``load_config``
is the canonical loader and is what callers use.)
"""
from __future__ import annotations

import json

from llm_code.runtime.config import load_config


def test_load_config_applies_model_upgrade(tmp_path):
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_dir.mkdir()
    project_dir.mkdir()
    (user_dir / "config.json").write_text(
        json.dumps({"model": "claude-3-opus-20240229"})
    )

    cfg = load_config(
        user_dir=user_dir,
        project_dir=project_dir,
        local_path=tmp_path / "missing.json",
        cli_overrides={},
    )
    assert cfg.model != "claude-3-opus-20240229"
    assert "claude-opus" in cfg.model

    # State file recorded under user_dir.
    state = json.loads((user_dir / "migration-state.json").read_text())
    assert "1.1.0-004" in state["applied"]


def test_load_config_unknown_model_passthrough(tmp_path):
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_dir.mkdir()
    project_dir.mkdir()
    (user_dir / "config.json").write_text(json.dumps({"model": "custom-local-model"}))

    cfg = load_config(
        user_dir=user_dir,
        project_dir=project_dir,
        local_path=tmp_path / "missing.json",
        cli_overrides={},
    )
    assert cfg.model == "custom-local-model"

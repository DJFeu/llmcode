"""Idempotency tests for the config migration system."""
from __future__ import annotations

import json

from llm_code.runtime.config_migration import (
    MIGRATION_REGISTRY,
    apply_pending_migrations,
)


def test_second_run_is_noop(tmp_path):
    cfg = {"model": "claude-3-opus-20240229"}
    apply_pending_migrations(cfg, config_dir=tmp_path)
    state_path = tmp_path / "migration-state.json"
    state_after_first = json.loads(state_path.read_text())

    # Second run on a fresh dict should not re-apply.
    cfg2 = {"model": "claude-3-opus-20240229"}
    out = apply_pending_migrations(cfg2, config_dir=tmp_path)
    assert out["model"] == "claude-3-opus-20240229"  # not rewritten this time
    assert "_migration_log" not in out
    state_after_second = json.loads(state_path.read_text())
    assert state_after_first == state_after_second


def test_all_registry_versions_recorded(tmp_path):
    apply_pending_migrations({}, config_dir=tmp_path)
    state = json.loads((tmp_path / "migration-state.json").read_text())
    assert set(state["applied"]) == {m.version for m in MIGRATION_REGISTRY}


def test_partial_state_only_runs_pending(tmp_path):
    state_path = tmp_path / "migration-state.json"
    state_path.write_text(json.dumps({"applied": ["1.1.0-001", "1.1.0-002", "1.1.0-003"]}))
    cfg = {"model": "claude-3-opus-20240229"}
    out = apply_pending_migrations(cfg, config_dir=tmp_path)
    # Only the model upgrade migration should have run.
    assert out["model"] != "claude-3-opus-20240229"
    state = json.loads(state_path.read_text())
    assert "1.1.0-004" in state["applied"]

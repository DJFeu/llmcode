"""Tests for config migration system."""
from __future__ import annotations

import json

from llm_code.runtime.config_migration import (
    MIGRATION_REGISTRY,
    Migration,
    apply_pending_migrations,
)


class TestMigrationRegistry:
    def test_registry_is_ordered(self):
        versions = [m.version for m in MIGRATION_REGISTRY]
        assert versions == sorted(versions)

    def test_all_migrations_have_required_fields(self):
        for m in MIGRATION_REGISTRY:
            assert m.version
            assert m.description
            assert callable(m.migrate)

    def test_no_duplicate_versions(self):
        versions = [m.version for m in MIGRATION_REGISTRY]
        assert len(versions) == len(set(versions))


class TestApplyPendingMigrations:
    def test_applies_all_migrations_on_fresh_config(self, tmp_path):
        cfg = {"model": "qwen"}
        result = apply_pending_migrations(cfg, config_dir=tmp_path)
        assert result["config_version"] == "1.1.0"
        assert "skill_router" in result
        assert "diminishing_returns" in result

    def test_idempotent_second_run(self, tmp_path):
        cfg = {"model": "qwen"}
        r1 = apply_pending_migrations(dict(cfg), config_dir=tmp_path)
        r2 = apply_pending_migrations(dict(r1), config_dir=tmp_path)
        assert r1 == r2

    def test_state_file_written(self, tmp_path):
        apply_pending_migrations({"model": "test"}, config_dir=tmp_path)
        state_path = tmp_path / "migration-state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert len(state["applied"]) == len(MIGRATION_REGISTRY)

    def test_skips_already_applied(self, tmp_path):
        # Pre-populate state with first migration
        state_path = tmp_path / "migration-state.json"
        state_path.write_text(json.dumps({"applied": ["1.1.0-001"]}))

        cfg = {"model": "test"}
        result = apply_pending_migrations(cfg, config_dir=tmp_path)
        # Should still apply remaining migrations
        assert "skill_router" in result
        assert "diminishing_returns" in result

        state = json.loads(state_path.read_text())
        assert len(state["applied"]) == len(MIGRATION_REGISTRY)

    def test_does_not_overwrite_existing_values(self, tmp_path):
        cfg = {
            "model": "test",
            "config_version": "2.0.0",  # already set
            "skill_router": {"enabled": False},  # already customized
        }
        result = apply_pending_migrations(cfg, config_dir=tmp_path)
        assert result["config_version"] == "2.0.0"  # not overwritten
        assert result["skill_router"]["enabled"] is False  # not overwritten

    def test_handles_corrupt_state_file(self, tmp_path):
        state_path = tmp_path / "migration-state.json"
        state_path.write_text("not json")
        cfg = {"model": "test"}
        result = apply_pending_migrations(cfg, config_dir=tmp_path)
        assert result["config_version"] == "1.1.0"

    def test_handles_missing_config_dir(self, tmp_path):
        nonexistent = tmp_path / "does" / "not" / "exist"
        cfg = {"model": "test"}
        result = apply_pending_migrations(cfg, config_dir=nonexistent)
        assert result["config_version"] == "1.1.0"

    def test_failed_migration_skipped(self, tmp_path):
        """A failing migration is skipped, others still apply."""

        def _bad_migrate(cfg):
            raise RuntimeError("boom")

        # Temporarily inject a bad migration (we test the mechanism, not the registry)
        import llm_code.runtime.config_migration as mod
        original = mod.MIGRATION_REGISTRY
        mod.MIGRATION_REGISTRY = (
            Migration("0.0.1-bad", "will fail", _bad_migrate),
            *original,
        )
        try:
            cfg = {"model": "test"}
            result = apply_pending_migrations(cfg, config_dir=tmp_path)
            # Good migrations still applied
            assert result["config_version"] == "1.1.0"
            state = json.loads((tmp_path / "migration-state.json").read_text())
            assert "0.0.1-bad" not in state["applied"]
        finally:
            mod.MIGRATION_REGISTRY = original

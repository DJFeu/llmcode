"""Tests for WorktreeConfig and its integration with SwarmConfig."""
from __future__ import annotations

import dataclasses
import pytest

from llm_code.runtime.config import SwarmConfig, WorktreeConfig


class TestWorktreeConfigDefaults:
    def test_on_complete_default(self):
        cfg = WorktreeConfig()
        assert cfg.on_complete == "diff"

    def test_base_dir_default(self):
        cfg = WorktreeConfig()
        assert cfg.base_dir == ""

    def test_copy_gitignored_default(self):
        cfg = WorktreeConfig()
        assert cfg.copy_gitignored == (".env", ".env.local")

    def test_cleanup_on_success_default(self):
        cfg = WorktreeConfig()
        assert cfg.cleanup_on_success is True


class TestWorktreeConfigFrozen:
    def test_frozen_on_complete(self):
        cfg = WorktreeConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.on_complete = "merge"  # type: ignore[misc]

    def test_frozen_base_dir(self):
        cfg = WorktreeConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.base_dir = "/tmp"  # type: ignore[misc]

    def test_frozen_copy_gitignored(self):
        cfg = WorktreeConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.copy_gitignored = (".env",)  # type: ignore[misc]


class TestSwarmConfigWorktreeField:
    def test_swarm_config_has_worktree(self):
        cfg = SwarmConfig()
        assert hasattr(cfg, "worktree")
        assert isinstance(cfg.worktree, WorktreeConfig)

    def test_swarm_config_worktree_defaults(self):
        cfg = SwarmConfig()
        assert cfg.worktree.on_complete == "diff"
        assert cfg.worktree.cleanup_on_success is True

    def test_swarm_config_worktree_custom(self):
        wt = WorktreeConfig(on_complete="merge", cleanup_on_success=False)
        cfg = SwarmConfig(worktree=wt)
        assert cfg.worktree.on_complete == "merge"
        assert cfg.worktree.cleanup_on_success is False

    def test_swarm_config_backend_worktree(self):
        cfg = SwarmConfig(backend="worktree")
        assert cfg.backend == "worktree"

    def test_swarm_config_instances_independent(self):
        cfg1 = SwarmConfig()
        cfg2 = SwarmConfig()
        assert cfg1.worktree is not cfg2.worktree

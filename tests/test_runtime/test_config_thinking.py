"""Tests for ThinkingConfig and its integration with RuntimeConfig."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime.config import (
    ThinkingConfig,
    RuntimeConfig,
    load_config,
)


class TestThinkingConfig:
    def test_defaults(self):
        t = ThinkingConfig()
        assert t.mode == "adaptive"
        assert t.budget_tokens == 10000

    def test_custom_values(self):
        t = ThinkingConfig(mode="enabled", budget_tokens=50000)
        assert t.mode == "enabled"
        assert t.budget_tokens == 50000

    def test_disabled_mode(self):
        t = ThinkingConfig(mode="disabled")
        assert t.mode == "disabled"

    def test_frozen(self):
        t = ThinkingConfig()
        with pytest.raises(Exception):
            t.mode = "enabled"  # type: ignore[misc]

    def test_invalid_mode_not_enforced_at_dataclass_level(self):
        # Dataclass itself does not validate; ConfigSchema does
        t = ThinkingConfig(mode="bogus")
        assert t.mode == "bogus"


class TestRuntimeConfigThinking:
    def test_default_thinking(self):
        cfg = RuntimeConfig()
        assert cfg.thinking.mode == "adaptive"
        assert cfg.thinking.budget_tokens == 10000

    def test_custom_thinking(self):
        cfg = RuntimeConfig(thinking=ThinkingConfig(mode="disabled", budget_tokens=0))
        assert cfg.thinking.mode == "disabled"


class TestLoadConfigThinking:
    def test_thinking_from_json(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        local = tmp_path / "local.json"
        local.write_text(json.dumps({
            "model": "test-model",
            "thinking": {"mode": "enabled", "budget_tokens": 25000},
        }))
        cfg = load_config(user_dir, project_dir, local, {})
        assert cfg.thinking.mode == "enabled"
        assert cfg.thinking.budget_tokens == 25000

    def test_thinking_defaults_when_absent(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        local = tmp_path / "local.json"
        local.write_text(json.dumps({"model": "test-model"}))
        cfg = load_config(user_dir, project_dir, local, {})
        assert cfg.thinking.mode == "adaptive"
        assert cfg.thinking.budget_tokens == 10000

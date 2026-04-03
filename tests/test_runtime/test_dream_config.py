"""Tests for DreamConfig and its integration with RuntimeConfig."""
from __future__ import annotations

import dataclasses
import json

import pytest

from llm_code.runtime.config import DreamConfig, RuntimeConfig, load_config


class TestDreamConfig:
    def test_defaults(self):
        dc = DreamConfig()
        assert dc.enabled is True
        assert dc.min_turns == 3

    def test_frozen(self):
        dc = DreamConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            dc.enabled = False  # type: ignore[misc]

    def test_custom_values(self):
        dc = DreamConfig(enabled=False, min_turns=10)
        assert dc.enabled is False
        assert dc.min_turns == 10

    def test_runtime_config_has_dream(self):
        rc = RuntimeConfig()
        assert isinstance(rc.dream, DreamConfig)
        assert rc.dream.enabled is True


class TestDreamConfigLoading:
    def test_loads_dream_from_json(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "dream": {
                "enabled": False,
                "min_turns": 7,
            }
        }))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.dream.enabled is False
        assert rc.dream.min_turns == 7

    def test_missing_dream_uses_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"model": "test"}))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.dream.enabled is True
        assert rc.dream.min_turns == 3

    def test_partial_dream_config_fills_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"dream": {"min_turns": 1}}))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.dream.enabled is True
        assert rc.dream.min_turns == 1

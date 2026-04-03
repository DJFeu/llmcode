"""Tests for computer-use configuration."""
from __future__ import annotations

import dataclasses

import pytest

from llm_code.runtime.config import ComputerUseConfig, RuntimeConfig, load_config


class TestComputerUseConfig:
    def test_defaults(self):
        cfg = ComputerUseConfig()
        assert cfg.enabled is False
        assert cfg.screenshot_delay == 0.5

    def test_frozen(self):
        cfg = ComputerUseConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.enabled = True  # type: ignore[misc]

    def test_custom_values(self):
        cfg = ComputerUseConfig(enabled=True, screenshot_delay=1.0)
        assert cfg.enabled is True
        assert cfg.screenshot_delay == 1.0

    def test_runtime_config_has_computer_use(self):
        rc = RuntimeConfig()
        assert isinstance(rc.computer_use, ComputerUseConfig)
        assert rc.computer_use.enabled is False


class TestComputerUseConfigLoading:
    def test_loads_from_json(self, tmp_path):
        import json

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "computer_use": {
                "enabled": True,
                "screenshot_delay": 1.5,
            }
        }))
        rc = load_config(
            user_dir=tmp_path,
            project_dir=tmp_path / "nonexistent",
            local_path=tmp_path / "nonexistent" / "config.json",
            cli_overrides={},
        )
        assert rc.computer_use.enabled is True
        assert rc.computer_use.screenshot_delay == 1.5

    def test_defaults_when_missing(self, tmp_path):
        import json

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({}))
        rc = load_config(
            user_dir=tmp_path,
            project_dir=tmp_path / "nonexistent",
            local_path=tmp_path / "nonexistent" / "config.json",
            cli_overrides={},
        )
        assert rc.computer_use.enabled is False
        assert rc.computer_use.screenshot_delay == 0.5

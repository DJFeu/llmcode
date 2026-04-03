"""Tests for IDE configuration."""
from __future__ import annotations

import dataclasses
import json

import pytest

from llm_code.runtime.config import IDEConfig, RuntimeConfig, load_config


class TestIDEConfig:
    def test_defaults(self):
        ic = IDEConfig()
        assert ic.enabled is False
        assert ic.port == 9876

    def test_frozen(self):
        ic = IDEConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ic.enabled = True  # type: ignore[misc]

    def test_custom_port(self):
        ic = IDEConfig(enabled=True, port=8888)
        assert ic.enabled is True
        assert ic.port == 8888

    def test_runtime_config_has_ide(self):
        rc = RuntimeConfig()
        assert isinstance(rc.ide, IDEConfig)
        assert rc.ide.enabled is False


class TestIDEConfigLoading:
    def test_loads_ide_from_json(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "ide": {
                "enabled": True,
                "port": 7777,
            }
        }))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.ide.enabled is True
        assert rc.ide.port == 7777

    def test_missing_ide_uses_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"model": "test"}))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.ide.enabled is False
        assert rc.ide.port == 9876

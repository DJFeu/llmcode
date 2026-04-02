"""Tests for runtime config loading and merging."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime.config import (
    HookConfig,
    RuntimeConfig,
    VisionConfig,
    load_config,
    merge_configs,
)


class TestHookConfig:
    def test_required_fields(self):
        h = HookConfig(event="pre_tool_use", command="echo hi")
        assert h.event == "pre_tool_use"
        assert h.command == "echo hi"
        assert h.tool_pattern == "*"

    def test_custom_pattern(self):
        h = HookConfig(event="post_tool_use", command="./check.sh", tool_pattern="bash_*")
        assert h.tool_pattern == "bash_*"

    def test_frozen(self):
        h = HookConfig(event="on_stop", command="cleanup.sh")
        with pytest.raises(Exception):
            h.event = "changed"  # type: ignore[misc]


class TestVisionConfig:
    def test_defaults(self):
        v = VisionConfig()
        assert v.fallback == ""
        assert v.vision_model == ""
        assert v.vision_api == ""
        assert v.vision_api_key_env == ""

    def test_custom(self):
        v = VisionConfig(vision_model="llava", vision_api="http://localhost:11434")
        assert v.vision_model == "llava"
        assert v.vision_api == "http://localhost:11434"


class TestRuntimeConfigDefaults:
    def test_default_values(self):
        cfg = RuntimeConfig()
        assert cfg.model == ""
        assert cfg.provider_base_url is None
        assert cfg.provider_api_key_env == "LLM_API_KEY"
        assert cfg.permission_mode == "prompt"
        assert cfg.max_turn_iterations == 10
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.7
        assert cfg.hooks == ()
        assert cfg.allowed_tools == frozenset()
        assert cfg.denied_tools == frozenset()
        assert cfg.compact_after_tokens == 80000
        assert cfg.timeout == 120.0
        assert cfg.max_retries == 2
        assert cfg.native_tools is True
        assert isinstance(cfg.vision, VisionConfig)

    def test_frozen(self):
        cfg = RuntimeConfig()
        with pytest.raises(Exception):
            cfg.model = "changed"  # type: ignore[misc]

    def test_hooks_are_tuple(self):
        h = HookConfig(event="pre_tool_use", command="echo")
        cfg = RuntimeConfig(hooks=(h,))
        assert isinstance(cfg.hooks, tuple)
        assert cfg.hooks[0] is h

    def test_allowed_denied_are_frozenset(self):
        cfg = RuntimeConfig(allowed_tools=frozenset({"read_file"}), denied_tools=frozenset({"bash"}))
        assert "read_file" in cfg.allowed_tools
        assert "bash" in cfg.denied_tools


class TestMergeConfigs:
    def test_flat_merge_override_wins(self):
        base = {"model": "qwen", "temperature": 0.5}
        override = {"temperature": 0.9}
        result = merge_configs(base, override)
        assert result["model"] == "qwen"
        assert result["temperature"] == 0.9

    def test_flat_merge_base_preserved(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = merge_configs(base, override)
        assert result["a"] == 1

    def test_nested_merge(self):
        base = {"provider": {"base_url": "http://old", "api_key_env": "KEY"}}
        override = {"provider": {"base_url": "http://new"}}
        result = merge_configs(base, override)
        assert result["provider"]["base_url"] == "http://new"
        assert result["provider"]["api_key_env"] == "KEY"

    def test_nested_override_non_dict_replaces(self):
        base = {"hooks": [{"event": "pre_tool_use", "command": "old"}]}
        override = {"hooks": [{"event": "post_tool_use", "command": "new"}]}
        result = merge_configs(base, override)
        assert result["hooks"][0]["command"] == "new"

    def test_empty_override(self):
        base = {"model": "qwen"}
        result = merge_configs(base, {})
        assert result["model"] == "qwen"

    def test_empty_base(self):
        override = {"model": "gpt4"}
        result = merge_configs({}, override)
        assert result["model"] == "gpt4"

    def test_does_not_mutate_base(self):
        base = {"model": "qwen", "nested": {"x": 1}}
        override = {"nested": {"x": 99}}
        merge_configs(base, override)
        assert base["nested"]["x"] == 1


class TestLoadConfig:
    def test_empty_dirs_returns_defaults(self, tmp_path):
        cfg = load_config(
            user_dir=tmp_path / "nonexistent_user",
            project_dir=tmp_path / "nonexistent_project",
            local_path=tmp_path / "nonexistent_local.json",
            cli_overrides={},
        )
        assert isinstance(cfg, RuntimeConfig)
        assert cfg.model == ""

    def test_user_config_loaded(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "model": "qwen3",
            "provider": {"base_url": "http://localhost:11434/v1"},
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert cfg.model == "qwen3"
        assert cfg.provider_base_url == "http://localhost:11434/v1"

    def test_project_overrides_user(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({"model": "user_model", "temperature": 0.5}))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "config.json").write_text(json.dumps({"model": "project_model"}))

        cfg = load_config(
            user_dir=user_dir,
            project_dir=project_dir,
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert cfg.model == "project_model"
        assert cfg.temperature == 0.5  # preserved from user

    def test_local_overrides_project(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "config.json").write_text(json.dumps({"model": "project_model"}))

        local_path = tmp_path / "local.json"
        local_path.write_text(json.dumps({"model": "local_model"}))

        cfg = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=project_dir,
            local_path=local_path,
            cli_overrides={},
        )
        assert cfg.model == "local_model"

    def test_cli_overrides_win(self, tmp_path):
        cfg = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={"model": "cli_model", "temperature": 0.1},
        )
        assert cfg.model == "cli_model"
        assert cfg.temperature == 0.1

    def test_hooks_parsed(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "hooks": [
                {"event": "pre_tool_use", "command": "echo pre", "tool_pattern": "bash"},
                {"event": "on_stop", "command": "cleanup.sh"},
            ]
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert len(cfg.hooks) == 2
        assert cfg.hooks[0].event == "pre_tool_use"
        assert cfg.hooks[0].tool_pattern == "bash"
        assert cfg.hooks[1].event == "on_stop"

    def test_permissions_mode_mapped(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "permissions": {"mode": "auto_accept"},
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert cfg.permission_mode == "auto_accept"

    def test_permissions_allow_deny_tools(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "permissions": {
                "allow_tools": ["read_file", "list_dir"],
                "deny_tools": ["bash"],
            },
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert "read_file" in cfg.allowed_tools
        assert "bash" in cfg.denied_tools

    def test_provider_api_key_env_mapped(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "provider": {"api_key_env": "MY_CUSTOM_KEY"},
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert cfg.provider_api_key_env == "MY_CUSTOM_KEY"

    def test_vision_config_nested(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "config.json").write_text(json.dumps({
            "vision": {"vision_model": "llava", "fallback": "text"},
        }))
        cfg = load_config(
            user_dir=user_dir,
            project_dir=tmp_path / "no_project",
            local_path=tmp_path / "no_local.json",
            cli_overrides={},
        )
        assert cfg.vision.vision_model == "llava"
        assert cfg.vision.fallback == "text"

    def test_missing_files_ok(self, tmp_path):
        """All paths missing should return default config without error."""
        cfg = load_config(
            user_dir=tmp_path / "missing_user",
            project_dir=tmp_path / "missing_project",
            local_path=tmp_path / "missing_local.json",
            cli_overrides={},
        )
        assert cfg.permission_mode == "prompt"

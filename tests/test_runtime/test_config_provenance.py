"""Tests for config loading with source provenance."""
from __future__ import annotations

import json

from llm_code.runtime.config import load_config_with_provenance


def test_load_config_with_provenance_tracks_winning_sources(tmp_path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "config.json").write_text(
        json.dumps({
            "model": "user-model",
            "provider": {
                "base_url": "http://user/v1",
                "api_key_env": "USER_KEY",
            },
            "permissions": {"mode": "prompt"},
        }),
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "config.json").write_text(
        json.dumps({
            "model": "project-model",
            "provider": {"base_url": "http://project/v1"},
        }),
        encoding="utf-8",
    )

    local_path = project_dir / "config.local.json"
    local_path.write_text(
        json.dumps({"permissions": {"mode": "workspace_write"}}),
        encoding="utf-8",
    )

    result = load_config_with_provenance(
        user_dir=user_dir,
        project_dir=project_dir,
        local_path=local_path,
        cli_overrides={"model": "cli-model"},
    )

    assert result.config.model == "cli-model"
    assert result.config.provider_base_url == "http://project/v1"
    assert result.config.provider_api_key_env == "USER_KEY"
    assert result.config.permission_mode == "workspace_write"

    assert result.sources["model"].label == "cli"
    assert result.sources["provider.base_url"].label == "project"
    assert result.sources["provider.api_key_env"].label == "user"
    assert result.sources["permissions.mode"].label == "local"


def test_load_config_with_provenance_keeps_merged_raw_dict(tmp_path):
    result = load_config_with_provenance(
        user_dir=tmp_path / "missing-user",
        project_dir=tmp_path / "missing-project",
        local_path=tmp_path / "missing-local.json",
        cli_overrides={"provider": {"base_url": "http://cli/v1"}},
    )

    assert result.raw == {"provider": {"base_url": "http://cli/v1"}}
    assert result.sources["provider.base_url"].label == "cli"

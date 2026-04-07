"""Tests for the 1.1.0-004 deprecated model ID upgrade migration."""
from __future__ import annotations

from llm_code.runtime.config_migration import (
    _DEPRECATED_MODEL_MAP,
    _migration_1_1_0_004_model_upgrade,
    apply_pending_migrations,
)


def test_top_level_model_rewritten():
    cfg = {"model": "claude-3-opus-20240229"}
    out = _migration_1_1_0_004_model_upgrade(cfg)
    assert out["model"] == _DEPRECATED_MODEL_MAP["claude-3-opus-20240229"]
    assert out["_migration_log"]["1.1.0-004_model_upgrade"]["model"] == "claude-3-opus-20240229"


def test_subagent_model_rewritten():
    cfg = {"subagent": {"model": "claude-3-haiku-20240307"}}
    out = _migration_1_1_0_004_model_upgrade(cfg)
    assert out["subagent"]["model"] == _DEPRECATED_MODEL_MAP["claude-3-haiku-20240307"]
    assert "subagent.model" in out["_migration_log"]["1.1.0-004_model_upgrade"]


def test_model_routing_fields_rewritten():
    cfg = {
        "model_routing": {
            "sub_agent": "claude-3-5-sonnet-20240620",
            "compaction": "claude-3-haiku-20240307",
            "fallback": "qwen-2.5-coder",
        }
    }
    out = _migration_1_1_0_004_model_upgrade(cfg)
    assert out["model_routing"]["sub_agent"] == _DEPRECATED_MODEL_MAP["claude-3-5-sonnet-20240620"]
    assert out["model_routing"]["compaction"] == _DEPRECATED_MODEL_MAP["claude-3-haiku-20240307"]
    assert out["model_routing"]["fallback"] == _DEPRECATED_MODEL_MAP["qwen-2.5-coder"]


def test_unknown_model_left_alone():
    cfg = {"model": "some-future-model-2030"}
    out = _migration_1_1_0_004_model_upgrade(cfg)
    assert out["model"] == "some-future-model-2030"
    assert "_migration_log" not in out


def test_qwen_models_rewritten():
    cfg = {"model": "qwen2.5-coder-32b"}
    out = _migration_1_1_0_004_model_upgrade(cfg)
    assert out["model"] == _DEPRECATED_MODEL_MAP["qwen2.5-coder-32b"]


def test_full_pipeline_applies_migration(tmp_path):
    cfg = {"model": "claude-3-opus-20240229"}
    out = apply_pending_migrations(cfg, config_dir=tmp_path)
    assert out["model"] == _DEPRECATED_MODEL_MAP["claude-3-opus-20240229"]

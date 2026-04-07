"""Tests for ConfigSchema JSON schema export and config presets."""
from __future__ import annotations

import json

from llm_code.runtime.config import ConfigSchema
from llm_code.runtime.config_presets import available_presets, load_preset


def test_config_schema_generates_json_schema() -> None:
    schema = ConfigSchema.model_json_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
    # Spot-check a few expected top-level keys
    props = schema["properties"]
    for key in ("model", "provider", "permissions", "hooks", "thinking"):
        assert key in props, f"missing key: {key}"


def test_config_schema_serializable() -> None:
    schema = ConfigSchema.model_json_schema()
    # Must round-trip via JSON without errors
    text = json.dumps(schema)
    assert json.loads(text) == schema


def test_available_presets_lists_all_four() -> None:
    presets = available_presets()
    for name in ("local-qwen", "claude-cloud", "mixed-routing", "cost-saving"):
        assert name in presets, f"missing preset: {name}"


def test_load_preset_local_qwen_has_model() -> None:
    data = load_preset("local-qwen")
    assert data is not None
    assert data.get("model")


def test_load_preset_claude_cloud_has_model() -> None:
    data = load_preset("claude-cloud")
    assert data is not None
    assert data.get("model")


def test_load_preset_mixed_routing_has_model() -> None:
    data = load_preset("mixed-routing")
    assert data is not None
    assert data.get("model")


def test_load_preset_cost_saving_has_model() -> None:
    data = load_preset("cost-saving")
    assert data is not None
    assert data.get("model")


def test_load_preset_unknown_returns_none() -> None:
    assert load_preset("does-not-exist") is None

"""Tests for SwarmConfig.role_models field."""
from __future__ import annotations

import dataclasses
import pytest

from llm_code.runtime.config import SwarmConfig


def test_role_models_default_empty_dict():
    """SwarmConfig.role_models defaults to an empty dict."""
    cfg = SwarmConfig()
    assert cfg.role_models == {}


def test_role_models_with_values():
    """SwarmConfig.role_models accepts a dict of role -> model mappings."""
    mapping = {"security": "qwen-fast", "reviewer": "qwen-large"}
    cfg = SwarmConfig(role_models=mapping)
    assert cfg.role_models == mapping
    assert cfg.role_models["security"] == "qwen-fast"
    assert cfg.role_models["reviewer"] == "qwen-large"


def test_swarm_config_frozen():
    """SwarmConfig is frozen — direct attribute assignment raises FrozenInstanceError."""
    cfg = SwarmConfig()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        cfg.role_models = {"new_role": "some-model"}  # type: ignore[misc]


def test_role_models_isolated_from_defaults():
    """Two SwarmConfig instances share no mutable state."""
    cfg1 = SwarmConfig()
    cfg2 = SwarmConfig()
    # They should be independent objects
    assert cfg1.role_models is not cfg2.role_models

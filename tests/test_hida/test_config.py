"""Tests for HidaConfig integration with RuntimeConfig."""
from __future__ import annotations

import pytest

from llm_code.runtime.config import HidaConfig, RuntimeConfig


class TestHidaConfig:
    def test_frozen(self):
        config = HidaConfig()
        with pytest.raises(AttributeError):
            config.enabled = False  # type: ignore[misc]

    def test_defaults(self):
        config = HidaConfig()
        assert config.enabled is False
        assert config.confidence_threshold == 0.6
        assert config.custom_profiles == ()

    def test_custom_values(self):
        config = HidaConfig(
            enabled=True,
            confidence_threshold=0.8,
            custom_profiles=({"task_type": "coding", "tools": ["bash"]},),
        )
        assert config.enabled is True
        assert config.confidence_threshold == 0.8
        assert len(config.custom_profiles) == 1


class TestRuntimeConfigHida:
    def test_runtime_config_has_hida(self):
        rc = RuntimeConfig()
        assert hasattr(rc, "hida")
        assert isinstance(rc.hida, HidaConfig)

    def test_runtime_config_hida_defaults(self):
        rc = RuntimeConfig()
        assert rc.hida.enabled is False

"""Tests for KnowledgeConfig."""
from __future__ import annotations


def test_knowledge_config_defaults():
    from llm_code.runtime.config import KnowledgeConfig

    cfg = KnowledgeConfig()
    assert cfg.enabled is True
    assert cfg.compile_on_exit is True
    assert cfg.max_context_tokens == 3000
    assert cfg.compile_model == ""


def test_knowledge_config_custom():
    from llm_code.runtime.config import KnowledgeConfig

    cfg = KnowledgeConfig(enabled=False, compile_model="qwen3.5")
    assert cfg.enabled is False
    assert cfg.compile_model == "qwen3.5"


def test_knowledge_config_frozen():
    from llm_code.runtime.config import KnowledgeConfig

    cfg = KnowledgeConfig()
    try:
        cfg.enabled = False  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_runtime_config_has_knowledge():
    from llm_code.runtime.config import RuntimeConfig, KnowledgeConfig

    cfg = RuntimeConfig()
    assert isinstance(cfg.knowledge, KnowledgeConfig)
    assert cfg.knowledge.enabled is True


def test_runtime_config_from_dict_knowledge():
    from llm_code.runtime.config import _dict_to_runtime_config

    data = {
        "knowledge": {
            "enabled": False,
            "compile_on_exit": False,
            "max_context_tokens": 5000,
            "compile_model": "qwen3.5",
        }
    }
    cfg = _dict_to_runtime_config(data)
    assert cfg.knowledge.enabled is False
    assert cfg.knowledge.compile_on_exit is False
    assert cfg.knowledge.max_context_tokens == 5000
    assert cfg.knowledge.compile_model == "qwen3.5"

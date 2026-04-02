import json
from llm_code.runtime.config import ModelRoutingConfig, RuntimeConfig, load_config


def test_model_routing_config_defaults():
    cfg = ModelRoutingConfig()
    assert cfg.sub_agent == ""
    assert cfg.compaction == ""


def test_model_routing_config_values():
    cfg = ModelRoutingConfig(sub_agent="qwen-32b", compaction="qwen-7b")
    assert cfg.sub_agent == "qwen-32b"


def test_runtime_config_has_model_routing():
    cfg = RuntimeConfig()
    assert isinstance(cfg.model_routing, ModelRoutingConfig)


def test_load_config_with_model_routing(tmp_path):
    config_dir = tmp_path / "user"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "model": "qwen3.5-122b",
        "model_routing": {
            "sub_agent": "qwen3.5-32b",
            "compaction": "qwen3.5-7b"
        }
    }))
    cfg = load_config(user_dir=config_dir, project_dir=tmp_path, local_path=tmp_path / "none.json", cli_overrides={})
    assert cfg.model_routing.sub_agent == "qwen3.5-32b"
    assert cfg.model_routing.compaction == "qwen3.5-7b"


def test_load_config_without_model_routing(tmp_path):
    cfg = load_config(user_dir=tmp_path, project_dir=tmp_path, local_path=tmp_path / "x.json", cli_overrides={})
    assert cfg.model_routing.sub_agent == ""

"""Provider-map routing for opencode-style ``provider/model`` refs."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


def _write_config(tmp_path: Path, data: dict) -> Path:
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "config.json").write_text(json.dumps(data))
    return user_dir


def test_provider_map_config_keeps_legacy_provider_fields(tmp_path: Path) -> None:
    from llm_code.runtime.config import load_config

    user_dir = _write_config(
        tmp_path,
        {
            "model": "deepseek",
            "provider": {
                "base_url": "https://deepseek.example/v1",
                "api_key_env": "LOCAL_LLM_API_KEY",
            },
        },
    )

    cfg = load_config(
        user_dir=user_dir,
        project_dir=tmp_path / "missing-project",
        local_path=tmp_path / "missing-local.json",
        cli_overrides={},
    )

    assert cfg.provider_base_url == "https://deepseek.example/v1"
    assert cfg.provider_api_key_env == "LOCAL_LLM_API_KEY"
    assert cfg.provider_map == {}


def test_opencode_provider_map_config_is_parsed(tmp_path: Path) -> None:
    from llm_code.runtime.config import load_config

    user_dir = _write_config(
        tmp_path,
        {
            "model": "planner/deepseek",
            "small_model": "worker/llama",
            "provider": {
                "planner": {
                    "name": "DeepSeek",
                    "options": {
                        "baseURL": "https://deepseek.example/v1",
                        "apiKey": "{env:LOCAL_LLM_API_KEY}",
                    },
                    "models": {"deepseek": {"name": "DeepSeek-R1"}},
                },
                "worker": {
                    "name": "Llama",
                    "options": {
                        "baseURL": "https://llama.example/v1",
                        "apiKey": "{env:LOCAL_LLM_API_KEY}",
                    },
                    "models": {"llama": {"name": "Llama-3.3"}},
                },
            },
        },
    )

    cfg = load_config(
        user_dir=user_dir,
        project_dir=tmp_path / "missing-project",
        local_path=tmp_path / "missing-local.json",
        cli_overrides={},
    )

    assert cfg.model == "planner/deepseek"
    assert cfg.small_model == "worker/llama"
    assert cfg.provider_map["planner"].base_url == "https://deepseek.example/v1"
    assert cfg.provider_map["planner"].api_key_env == "LOCAL_LLM_API_KEY"
    assert cfg.provider_map["worker"].base_url == "https://llama.example/v1"
    assert cfg.provider_map["worker"].models == {"llama": {"name": "Llama-3.3"}}
    assert cfg.model_routing.sub_agent == "worker/llama"
    assert cfg.model_routing.compaction == "worker/llama"


def test_explicit_model_routing_wins_over_small_model(tmp_path: Path) -> None:
    from llm_code.runtime.config import load_config

    user_dir = _write_config(
        tmp_path,
        {
            "model": "planner/deepseek",
            "small_model": "worker/llama",
            "provider": {
                "planner": {"options": {"baseURL": "https://deepseek.example/v1"}},
                "worker": {"options": {"baseURL": "https://llama.example/v1"}},
            },
            "model_routing": {
                "sub_agent": "planner/deepseek",
                "compaction": "planner/deepseek",
            },
        },
    )

    cfg = load_config(
        user_dir=user_dir,
        project_dir=tmp_path / "missing-project",
        local_path=tmp_path / "missing-local.json",
        cli_overrides={},
    )

    assert cfg.model_routing.sub_agent == "planner/deepseek"
    assert cfg.model_routing.compaction == "planner/deepseek"


def test_resolve_provider_target_uses_provider_map_for_known_provider(tmp_path: Path) -> None:
    from llm_code.runtime.config import load_config
    from llm_code.runtime.provider_routing import resolve_provider_target

    user_dir = _write_config(
        tmp_path,
        {
            "model": "planner/deepseek",
            "provider": {
                "planner": {
                    "options": {
                        "baseURL": "https://deepseek.example/v1",
                        "apiKey": "{env:LOCAL_LLM_API_KEY}",
                    },
                },
                "worker": {
                    "options": {
                        "baseURL": "https://llama.example/v1",
                        "apiKey": "{env:LOCAL_LLM_API_KEY}",
                    },
                },
            },
        },
    )
    cfg = load_config(
        user_dir=user_dir,
        project_dir=tmp_path / "missing-project",
        local_path=tmp_path / "missing-local.json",
        cli_overrides={},
    )

    target = resolve_provider_target(cfg, "worker/llama")

    assert target.logical_model == "worker/llama"
    assert target.request_model == "llama"
    assert target.provider_id == "worker"
    assert target.base_url == "https://llama.example/v1"
    assert target.api_key_env == "LOCAL_LLM_API_KEY"
    assert target.uses_provider_map is True


def test_resolve_provider_target_does_not_split_unknown_slash_model() -> None:
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.runtime.provider_routing import resolve_provider_target

    cfg = RuntimeConfig(
        model="deepseek-ai/deepseek-coder",
        provider_base_url="https://legacy.example/v1",
    )

    target = resolve_provider_target(cfg)

    assert target.logical_model == "deepseek-ai/deepseek-coder"
    assert target.request_model == "deepseek-ai/deepseek-coder"
    assert target.provider_id == ""
    assert target.base_url == "https://legacy.example/v1"
    assert target.uses_provider_map is False


def test_resolve_profile_for_target_prefers_logical_provider_ref(monkeypatch) -> None:
    from llm_code.runtime.model_profile import ModelProfile
    from llm_code.runtime.provider_routing import (
        ProviderTarget,
        resolve_profile_for_target,
    )

    seen: list[str] = []

    def fake_get_profile(model: str) -> ModelProfile:
        seen.append(model)
        if model == "planner/deepseek":
            return ModelProfile(name="Logical DeepSeek")
        return ModelProfile(name="(default)")

    monkeypatch.setattr("llm_code.runtime.model_profile.get_profile", fake_get_profile)

    profile = resolve_profile_for_target(
        ProviderTarget(
            logical_model="planner/deepseek",
            request_model="deepseek",
            provider_id="planner",
            base_url="https://deepseek.example/v1",
            api_key_env="LOCAL_LLM_API_KEY",
            uses_provider_map=True,
        )
    )

    assert profile.name == "Logical DeepSeek"
    assert seen == ["planner/deepseek"]


def test_resolve_profile_for_target_falls_back_to_request_model(monkeypatch) -> None:
    from llm_code.runtime.model_profile import ModelProfile
    from llm_code.runtime.provider_routing import (
        ProviderTarget,
        resolve_profile_for_target,
    )

    seen: list[str] = []

    def fake_get_profile(model: str) -> ModelProfile:
        seen.append(model)
        if model == "deepseek":
            return ModelProfile(name="DeepSeek Builtin")
        return ModelProfile(name="(default)")

    monkeypatch.setattr("llm_code.runtime.model_profile.get_profile", fake_get_profile)

    profile = resolve_profile_for_target(
        ProviderTarget(
            logical_model="planner/deepseek",
            request_model="deepseek",
            provider_id="planner",
            base_url="https://deepseek.example/v1",
            api_key_env="LOCAL_LLM_API_KEY",
            uses_provider_map=True,
        )
    )

    assert profile.name == "DeepSeek Builtin"
    assert seen == ["planner/deepseek", "deepseek"]


def test_provider_map_request_models_use_generic_builtin_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from llm_code.runtime.config import ProviderEndpointConfig, RuntimeConfig
    from llm_code.runtime.model_profile import ProfileRegistry
    from llm_code.runtime.provider_routing import (
        resolve_provider_target,
        resolve_profile_for_target,
    )

    registry = ProfileRegistry(user_profile_dir=tmp_path / "no-user-profiles")
    monkeypatch.setattr("llm_code.runtime.model_profile.get_profile", registry.resolve)

    cfg = RuntimeConfig(
        model="planner/deepseek",
        provider_map={
            "planner": ProviderEndpointConfig(
                id="planner",
                base_url="https://deepseek.example/v1",
            ),
            "worker": ProviderEndpointConfig(
                id="worker",
                base_url="https://llama.example/v1",
            ),
        },
    )

    deepseek_profile = resolve_profile_for_target(
        resolve_provider_target(cfg, "planner/deepseek")
    )
    llama_profile = resolve_profile_for_target(
        resolve_provider_target(cfg, "worker/llama")
    )

    assert deepseek_profile.name == "DeepSeek"
    assert deepseek_profile.supports_reasoning is True
    assert llama_profile.name == "Meta Llama"
    assert llama_profile.supports_reasoning is False


def test_create_provider_for_model_sends_request_model_and_provider_url(monkeypatch) -> None:
    from llm_code.runtime.config import ProviderEndpointConfig, RuntimeConfig
    from llm_code.runtime.provider_routing import create_provider_for_model

    provider = MagicMock()
    from_model = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "llm_code.api.client.ProviderClient.from_model",
        from_model,
    )
    monkeypatch.setattr(
        "llm_code.runtime.auth.resolve_api_key",
        lambda env_var: f"key:{env_var}",
    )

    cfg = RuntimeConfig(
        model="planner/deepseek",
        provider_map={
            "worker": ProviderEndpointConfig(
                id="worker",
                base_url="https://llama.example/v1",
                api_key_env="LOCAL_LLM_API_KEY",
            )
        },
    )

    result = create_provider_for_model(cfg, "worker/llama")

    assert result is provider
    from_model.assert_called_once()
    kwargs = from_model.call_args.kwargs
    assert from_model.call_args.args == ()
    assert kwargs["model"] == "llama"
    assert kwargs["base_url"] == "https://llama.example/v1"
    assert kwargs["api_key"] == "key:LOCAL_LLM_API_KEY"


def test_app_state_builds_main_provider_from_provider_map(tmp_path: Path, monkeypatch) -> None:
    from llm_code.runtime.app_state import AppState
    from llm_code.runtime.config import ProviderEndpointConfig, RuntimeConfig

    provider = MagicMock()
    provider.supports_native_tools.return_value = False
    provider.supports_reasoning.return_value = False
    provider.supports_images.return_value = False
    from_model = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "llm_code.api.client.ProviderClient.from_model",
        from_model,
    )
    monkeypatch.setattr(
        "llm_code.runtime.auth.resolve_api_key",
        lambda env_var: f"key:{env_var}",
    )

    cfg = RuntimeConfig(
        model="planner/deepseek",
        provider_map={
            "planner": ProviderEndpointConfig(
                id="planner",
                base_url="https://deepseek.example/v1",
                api_key_env="LOCAL_LLM_API_KEY",
            )
        },
    )

    AppState.from_config(
        cfg,
        cwd=tmp_path,
        register_core_tools=lambda *_args, **_kwargs: None,
    )

    kwargs = from_model.call_args.kwargs
    assert kwargs["model"] == "deepseek"
    assert kwargs["base_url"] == "https://deepseek.example/v1"


def test_subagent_factory_builds_provider_for_requested_model(monkeypatch, tmp_path: Path) -> None:
    from llm_code.api.types import MessageResponse, TextBlock, TokenUsage
    from llm_code.runtime.config import ProviderEndpointConfig, RuntimeConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.runtime.subagent_factory import make_subagent_runtime
    from llm_code.tools.registry import ToolRegistry

    parent_provider = MagicMock(name="deepseek-provider")
    parent_provider.supports_native_tools.return_value = False
    parent_provider.supports_reasoning.return_value = False

    child_provider = MagicMock(name="llama-provider")
    child_provider.supports_native_tools.return_value = False
    child_provider.supports_reasoning.return_value = False
    child_provider.send_message.return_value = MessageResponse(
        content=(TextBlock(text="ok"),),
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        stop_reason="stop",
    )
    from_model = MagicMock(return_value=child_provider)
    monkeypatch.setattr(
        "llm_code.api.client.ProviderClient.from_model",
        from_model,
    )
    monkeypatch.setattr(
        "llm_code.runtime.auth.resolve_api_key",
        lambda env_var: f"key:{env_var}",
    )

    cfg = RuntimeConfig(
        model="planner/deepseek",
        provider_map={
            "worker": ProviderEndpointConfig(
                id="worker",
                base_url="https://llama.example/v1",
                api_key_env="LOCAL_LLM_API_KEY",
            )
        },
    )
    parent = ConversationRuntime(
        provider=parent_provider,
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.PROMPT),
        hook_runner=None,
        prompt_builder=SystemPromptBuilder(),
        config=cfg,
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        ),
    )

    child = make_subagent_runtime(parent, role=None, model="worker/llama")

    assert child._provider is child_provider
    kwargs = from_model.call_args.kwargs
    assert kwargs["model"] == "llama"
    assert kwargs["base_url"] == "https://llama.example/v1"


def test_subagent_factory_uses_role_model_routing(monkeypatch, tmp_path: Path) -> None:
    from llm_code.api.types import MessageResponse, TextBlock, TokenUsage
    from llm_code.runtime.config import (
        ModelRoutingConfig,
        ProviderEndpointConfig,
        RuntimeConfig,
    )
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.runtime.subagent_factory import make_subagent_runtime
    from llm_code.tools.agent_roles import GENERAL_ROLE
    from llm_code.tools.registry import ToolRegistry

    parent_provider = MagicMock(name="deepseek-provider")
    parent_provider.supports_native_tools.return_value = False
    parent_provider.supports_reasoning.return_value = False

    child_provider = MagicMock(name="llama-provider")
    child_provider.supports_native_tools.return_value = False
    child_provider.supports_reasoning.return_value = False
    child_provider.send_message.return_value = MessageResponse(
        content=(TextBlock(text="ok"),),
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        stop_reason="stop",
    )
    from_model = MagicMock(return_value=child_provider)
    monkeypatch.setattr(
        "llm_code.api.client.ProviderClient.from_model",
        from_model,
    )
    monkeypatch.setattr(
        "llm_code.runtime.auth.resolve_api_key",
        lambda env_var: f"key:{env_var}",
    )

    cfg = RuntimeConfig(
        model="planner/deepseek",
        model_routing=ModelRoutingConfig(sub_agent="worker/llama"),
        provider_map={
            "worker": ProviderEndpointConfig(
                id="worker",
                base_url="https://llama.example/v1",
                api_key_env="LOCAL_LLM_API_KEY",
            )
        },
    )
    parent = ConversationRuntime(
        provider=parent_provider,
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.PROMPT),
        hook_runner=None,
        prompt_builder=SystemPromptBuilder(),
        config=cfg,
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        ),
    )

    child = make_subagent_runtime(parent, role=GENERAL_ROLE, model=None)

    assert child._provider is child_provider
    assert child._active_model == "worker/llama"
    assert child._request_model == "llama"
    kwargs = from_model.call_args.kwargs
    assert kwargs["model"] == "llama"
    assert kwargs["base_url"] == "https://llama.example/v1"


def test_knowledge_rebuild_uses_compaction_provider(monkeypatch, tmp_path: Path) -> None:
    from llm_code.runtime.config import (
        KnowledgeConfig,
        ModelRoutingConfig,
        ProviderEndpointConfig,
        RuntimeConfig,
    )

    provider = MagicMock(name="llama-provider")
    create_provider = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "llm_code.runtime.provider_routing.create_provider_for_model",
        create_provider,
    )

    cfg = RuntimeConfig(
        model="planner/deepseek",
        model_routing=ModelRoutingConfig(compaction="worker/llama"),
        provider_map={
            "worker": ProviderEndpointConfig(
                id="worker",
                base_url="https://llama.example/v1",
                api_key_env="LOCAL_LLM_API_KEY",
            )
        },
        knowledge=KnowledgeConfig(compile_model=""),
    )

    # This mirrors dispatcher logic without running the full command UI.
    from llm_code.view.dispatcher import _knowledge_compile_provider

    compile_model, compile_provider = _knowledge_compile_provider(
        cfg,
        runtime_provider=MagicMock(name="deepseek-provider"),
    )

    assert compile_model == "worker/llama"
    assert compile_provider is provider
    create_provider.assert_called_once_with(cfg, "worker/llama")

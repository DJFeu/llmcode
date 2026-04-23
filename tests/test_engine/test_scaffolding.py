"""M0 scaffolding tests — verify the v12 engine package imports cleanly
and the core shared types + config dataclasses are constructible with
the defaults documented in the spec.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md
Plan: this file backs M0 of the v12 haystack-borrow overhaul.

Tests live in `tests/test_engine/`; the parity/ sibling directory is an
empty marker today and will be populated by M1-M7 parity suites.
"""
from __future__ import annotations

import dataclasses
from enum import Enum

import pytest


class TestEnginePackageImport:
    def test_engine_package_imports(self) -> None:
        import llm_code.engine as engine  # noqa: F401

    def test_engine_exports_state_memoryscope_mode(self) -> None:
        from llm_code.engine import MemoryScope, Mode, State

        assert State is not None
        assert MemoryScope is not None
        assert Mode is not None

    def test_engine_state_module_importable(self) -> None:
        from llm_code.engine import state as state_mod

        assert hasattr(state_mod, "State")
        assert hasattr(state_mod, "MemoryScope")


class TestMemoryScope:
    def test_memory_scope_is_str_enum(self) -> None:
        from llm_code.engine import MemoryScope

        assert issubclass(MemoryScope, str)
        assert issubclass(MemoryScope, Enum)

    def test_memory_scope_members(self) -> None:
        from llm_code.engine import MemoryScope

        assert MemoryScope.SESSION.value == "session"
        assert MemoryScope.PROJECT.value == "project"
        assert MemoryScope.GLOBAL.value == "global"

    def test_memory_scope_string_equality(self) -> None:
        from llm_code.engine import MemoryScope

        assert MemoryScope.SESSION == "session"
        assert MemoryScope.PROJECT == "project"

    def test_memory_scope_from_value(self) -> None:
        from llm_code.engine import MemoryScope

        assert MemoryScope("session") is MemoryScope.SESSION
        assert MemoryScope("project") is MemoryScope.PROJECT
        assert MemoryScope("global") is MemoryScope.GLOBAL

    def test_memory_scope_rejects_unknown_value(self) -> None:
        from llm_code.engine import MemoryScope

        with pytest.raises(ValueError):
            MemoryScope("unknown")


class TestStateTypedDict:
    def test_state_is_typed_dict(self) -> None:
        from llm_code.engine import State

        assert getattr(State, "__total__", None) is False

    def test_state_accepts_canonical_keys(self) -> None:
        from llm_code.engine import State

        s: State = {
            "messages": [],
            "tool_calls": [],
            "tool_results": [],
            "iteration": 0,
            "last_error": None,
            "degraded": False,
            "mode": "build",
            "denial_history": [],
            "memory_entries": [],
            "allowed_tools": frozenset(),
        }
        assert s["iteration"] == 0
        assert s["mode"] == "build"

    def test_state_empty_dict_valid(self) -> None:
        from llm_code.engine import State

        s: State = {}
        assert s == {}

    def test_state_partial_construction(self) -> None:
        from llm_code.engine import State

        s: State = {"iteration": 3, "degraded": True}
        assert s["iteration"] == 3
        assert s["degraded"] is True


class TestAgentLoopConfig:
    def test_defaults(self) -> None:
        from llm_code.runtime.config import AgentLoopConfig

        cfg = AgentLoopConfig()
        assert cfg.max_agent_steps == 50
        assert cfg.retry_policy == "no_retry"
        assert cfg.retry_max_attempts == 3
        assert cfg.fallback_policy == "none"
        assert cfg.degraded_policy == "none"
        assert cfg.degraded_threshold == 3
        assert cfg.exit_conditions == ("max_steps",)
        assert cfg.retry_budget == 20

    def test_frozen(self) -> None:
        from llm_code.runtime.config import AgentLoopConfig

        cfg = AgentLoopConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.max_agent_steps = 100  # type: ignore[misc]

    def test_custom_values_preserved(self) -> None:
        from llm_code.runtime.config import AgentLoopConfig

        cfg = AgentLoopConfig(
            max_agent_steps=30,
            retry_policy="exponential",
            exit_conditions=("max_steps", "no_progress"),
        )
        assert cfg.max_agent_steps == 30
        assert cfg.retry_policy == "exponential"
        assert cfg.exit_conditions == ("max_steps", "no_progress")


class TestObservabilityConfig:
    def test_defaults(self) -> None:
        from llm_code.runtime.config import ObservabilityConfig

        cfg = ObservabilityConfig()
        assert cfg.enabled is True
        assert cfg.exporter == "console"
        assert cfg.otlp_endpoint == ""
        assert cfg.otlp_protocol == "http/protobuf"
        assert cfg.otlp_headers == ()
        assert cfg.langfuse_public_key_env == "LANGFUSE_PUBLIC_KEY"
        assert cfg.langfuse_secret_key_env == "LANGFUSE_SECRET_KEY"
        assert cfg.langfuse_host == "https://cloud.langfuse.com"
        assert cfg.service_name == "llmcode"
        assert cfg.service_version == ""
        assert cfg.resource_attrs == ()
        assert cfg.sample_rate == 1.0
        assert cfg.redact_log_records is True
        assert cfg.redact_span_attributes is True
        assert cfg.metrics_enabled is True
        assert cfg.metrics_port == 0

    def test_frozen(self) -> None:
        from llm_code.runtime.config import ObservabilityConfig

        cfg = ObservabilityConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.exporter = "otlp"  # type: ignore[misc]

    def test_exporter_values_accept_all_valid(self) -> None:
        from llm_code.runtime.config import ObservabilityConfig

        for exporter in ("otlp", "langfuse", "console", "off"):
            cfg = ObservabilityConfig(exporter=exporter)
            assert cfg.exporter == exporter


class TestHayhooksConfig:
    def test_defaults(self) -> None:
        from llm_code.runtime.config import HayhooksConfig

        cfg = HayhooksConfig()
        assert cfg.enabled is False
        assert cfg.auth_token_env == "LLMCODE_HAYHOOKS_TOKEN"
        assert cfg.allowed_tools == ()
        assert cfg.max_agent_steps == 20
        assert cfg.request_timeout_s == 300.0
        assert cfg.rate_limit_rpm == 60
        assert cfg.enable_openai_compat is True
        assert cfg.enable_mcp is True
        assert cfg.enable_ide_rpc is True
        assert cfg.enable_debug_repl is False
        assert cfg.cors_origins == ()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8080

    def test_frozen(self) -> None:
        from llm_code.runtime.config import HayhooksConfig

        cfg = HayhooksConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.enabled = True  # type: ignore[misc]

    def test_security_defaults_are_local_only(self) -> None:
        """Hayhooks must default to 127.0.0.1 bind + debug REPL off."""
        from llm_code.runtime.config import HayhooksConfig

        cfg = HayhooksConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.enable_debug_repl is False


class TestEngineConfig:
    def test_defaults(self) -> None:
        from llm_code.runtime.config import (
            AgentLoopConfig,
            EngineConfig,
            HayhooksConfig,
            ObservabilityConfig,
        )

        cfg = EngineConfig()
        assert isinstance(cfg.agent_loop, AgentLoopConfig)
        assert isinstance(cfg.observability, ObservabilityConfig)
        assert isinstance(cfg.hayhooks, HayhooksConfig)
        assert cfg.pipeline_stages == (
            "perm",
            "denial",
            "rate",
            "speculative",
            "resolver",
            "exec",
            "post",
        )

    def test_frozen(self) -> None:
        from llm_code.runtime.config import EngineConfig

        cfg = EngineConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.pipeline_stages = ()  # type: ignore[misc]

    def test_nested_config_is_independent_instance(self) -> None:
        from llm_code.runtime.config import EngineConfig

        cfg_a = EngineConfig()
        cfg_b = EngineConfig()
        assert cfg_a.agent_loop is not cfg_b.agent_loop
        assert cfg_a.observability is not cfg_b.observability
        assert cfg_a.hayhooks is not cfg_b.hayhooks

    def test_pipeline_stages_includes_v11_components(self) -> None:
        """Default pipeline includes speculative + resolver (v11 features
        folded into M2 per spec §1.3)."""
        from llm_code.runtime.config import EngineConfig

        cfg = EngineConfig()
        assert "speculative" in cfg.pipeline_stages
        assert "resolver" in cfg.pipeline_stages

    def test_custom_stages_override(self) -> None:
        from llm_code.runtime.config import EngineConfig

        cfg = EngineConfig(pipeline_stages=("perm", "exec", "post"))
        assert cfg.pipeline_stages == ("perm", "exec", "post")

    def test_no_transitional_flag_fields(self) -> None:
        """The transitional v12 parity flag was deleted in v2.0 (M8.b).
        This guards against its accidental reintroduction."""
        from llm_code.runtime.config import EngineConfig

        fields = {f.name for f in dataclasses.fields(EngineConfig)}
        assert not any(f.startswith("_v12") for f in fields)


class TestLegacyConfigUnchanged:
    """M0 must be purely additive. Existing configs keep their shape."""

    def test_memory_config_unchanged(self) -> None:
        from llm_code.runtime.config import MemoryConfig

        cfg = MemoryConfig()
        assert cfg.strict_derivable_check is False

    def test_web_search_config_unchanged(self) -> None:
        from llm_code.runtime.config import WebSearchConfig

        cfg = WebSearchConfig()
        assert cfg.default_backend == "duckduckgo"
        assert cfg.serper_api_key_env == "SERPER_API_KEY"

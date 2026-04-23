"""Tests for ``build_default_pipeline`` — v12 M2 Task 2.8.

Asserts the seven-stage skeleton wires correctly, that custom stage
tuples opt components out, and that the memory hook comment is present
(M7 worker relies on the anchor).

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.8
"""
from __future__ import annotations


from llm_code.runtime.config import EngineConfig


class _FakeRegistry:
    def __init__(self, tools: dict | None = None) -> None:
        self._tools = tools or {}

    def get(self, name):
        return self._tools.get(name)


class TestBuildDefaultPipelineBasic:
    def test_importable(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        assert callable(build_default_pipeline)

    def test_default_config_builds(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        p = build_default_pipeline(EngineConfig(), registry=_FakeRegistry())
        assert p is not None

    def test_components_registered(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        p = build_default_pipeline(EngineConfig(), registry=_FakeRegistry())
        names = set(p._components.keys())
        assert {"perm", "denial", "rate", "speculative", "resolver", "exec", "post"} <= names

    def test_connections_exist(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        p = build_default_pipeline(EngineConfig(), registry=_FakeRegistry())
        pairs = {(s, d) for s, _, d, _ in p._connections}
        assert ("perm", "denial") in pairs
        assert ("denial", "rate") in pairs
        assert ("rate", "speculative") in pairs
        assert ("speculative", "resolver") in pairs
        assert ("resolver", "exec") in pairs
        assert ("exec", "post") in pairs

    def test_validate_succeeds(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        p = build_default_pipeline(EngineConfig(), registry=_FakeRegistry())
        # Must not raise — default wiring has no cycle, no state-write collisions.
        p.validate()


class TestBuildDefaultPipelineStageOptOut:
    def test_omit_speculative(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        cfg = EngineConfig(
            pipeline_stages=("perm", "denial", "rate", "resolver", "exec", "post"),
        )
        p = build_default_pipeline(cfg, registry=_FakeRegistry())
        assert "speculative" not in p._components

    def test_omit_rate(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        cfg = EngineConfig(
            pipeline_stages=("perm", "denial", "speculative", "resolver", "exec", "post"),
        )
        p = build_default_pipeline(cfg, registry=_FakeRegistry())
        assert "rate" not in p._components

    def test_minimal_stages(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline

        cfg = EngineConfig(pipeline_stages=("perm", "exec", "post"))
        p = build_default_pipeline(cfg, registry=_FakeRegistry())
        # perm + exec + post — no speculative/resolver/denial/rate.
        assert set(p._components) >= {"perm", "exec", "post"}
        assert "speculative" not in p._components


class TestBuildDefaultPipelineMemoryHookAnchor:
    def test_memory_hook_comment_present(self) -> None:
        """M7 worker grepably inserts memory Components just before the
        PromptAssembler stage — verify the anchor comment lives in the
        factory source so the insertion point cannot drift."""
        import inspect

        from llm_code.engine.default_pipeline import build_default_pipeline

        source = inspect.getsource(build_default_pipeline)
        assert "M7 HOOK: memory Components insert here" in source


class TestBuildDefaultPipelineCustomInjection:
    def test_policy_and_registry_wired(self) -> None:
        """Permission policy and tool registry flow into their components."""
        from llm_code.engine.default_pipeline import build_default_pipeline
        from llm_code.runtime.permissions import PermissionMode, PermissionPolicy

        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        registry = _FakeRegistry()
        p = build_default_pipeline(
            EngineConfig(), registry=registry, permission_policy=policy,
        )
        perm = p._components["perm"]
        assert perm._policy is policy

    def test_custom_denial_tracker_wired(self) -> None:
        from llm_code.engine.default_pipeline import build_default_pipeline
        from llm_code.runtime.permission_denial_tracker import (
            PermissionDenialTracker,
        )

        tracker = PermissionDenialTracker()
        p = build_default_pipeline(
            EngineConfig(), registry=_FakeRegistry(), denial_tracker=tracker,
        )
        assert p._components["denial"].tracker is tracker

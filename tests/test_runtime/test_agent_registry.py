"""Tests for the dynamic agent role registry (v16 M1)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from llm_code.runtime.agent_registry import (
    AgentRegistry,
    get_registry,
    reset_registry,
)
from llm_code.tools.agent_roles import BUILT_IN_ROLES, AgentRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_agent(dir_path: Path, name: str, body: str = "Custom body.") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    md = dir_path / f"{name}.md"
    md.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: synthetic role {name}\n"
        "tools:\n"
        "  - read_file\n"
        "  - grep_search\n"
        "model: sub_agent\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return md


@pytest.fixture
def fresh_registry() -> AgentRegistry:
    """Return a clean registry instance (no singleton bleed)."""
    return AgentRegistry()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` to a tmp dir so user-agent discovery is hermetic."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Built-in seeding
# ---------------------------------------------------------------------------


class TestBuiltInSeeding:
    def test_built_in_roles_present(self, fresh_registry: AgentRegistry) -> None:
        names = fresh_registry.list_names()
        for builtin in BUILT_IN_ROLES:
            assert builtin in names

    def test_resolve_returns_built_in(self, fresh_registry: AgentRegistry) -> None:
        role = fresh_registry.resolve("build")
        assert role is BUILT_IN_ROLES["build"]

    def test_unknown_returns_none(self, fresh_registry: AgentRegistry) -> None:
        assert fresh_registry.resolve("not-a-role") is None

    def test_empty_name_returns_none(self, fresh_registry: AgentRegistry) -> None:
        assert fresh_registry.resolve("") is None


# ---------------------------------------------------------------------------
# Discovery — user + project layers
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_user_layer(
        self, fresh_registry: AgentRegistry, fake_home: Path
    ) -> None:
        _write_agent(fake_home / ".llm-code" / "agents", "researcher")
        fresh_registry.discover()
        assert "researcher" in fresh_registry.list_names()
        role = fresh_registry.resolve("researcher")
        assert role is not None
        assert role.is_builtin is False

    def test_project_layer(
        self,
        fresh_registry: AgentRegistry,
        fake_home: Path,
        tmp_path: Path,
    ) -> None:
        project = tmp_path / "proj"
        _write_agent(project / ".llm-code" / "agents", "synth-role")
        fresh_registry.discover(project)
        assert "synth-role" in fresh_registry.list_names()

    def test_project_overrides_user(
        self,
        fresh_registry: AgentRegistry,
        fake_home: Path,
        tmp_path: Path,
    ) -> None:
        project = tmp_path / "proj"
        _write_agent(
            fake_home / ".llm-code" / "agents", "shared", body="from-user"
        )
        _write_agent(
            project / ".llm-code" / "agents", "shared", body="from-project"
        )
        fresh_registry.discover(project)
        role = fresh_registry.resolve("shared")
        assert role is not None
        assert "from-project" in role.system_prompt_prefix


# ---------------------------------------------------------------------------
# Built-in shadow detection
# ---------------------------------------------------------------------------


class TestBuiltInCollision:
    def test_user_shadows_built_in(
        self,
        fresh_registry: AgentRegistry,
        fake_home: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _write_agent(
            fake_home / ".llm-code" / "agents", "build", body="custom-build"
        )
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.agent_registry"):
            fresh_registry.discover()
        # Custom wins.
        role = fresh_registry.resolve("build")
        assert role is not None
        assert "custom-build" in role.system_prompt_prefix
        # Warning was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("shadows built-in" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# register / reset
# ---------------------------------------------------------------------------


class TestRegisterReset:
    def test_register_adds_role(self, fresh_registry: AgentRegistry) -> None:
        role = AgentRole(
            name="dynamic",
            description="manually registered",
            system_prompt_prefix="dynamic body",
            allowed_tools=frozenset({"read_file"}),
            model_key="sub_agent",
            is_builtin=False,
        )
        fresh_registry.register(role)
        assert fresh_registry.resolve("dynamic") is role

    def test_register_rejects_empty_name(
        self, fresh_registry: AgentRegistry
    ) -> None:
        with pytest.raises(ValueError, match="non-empty name"):
            fresh_registry.register(
                AgentRole(
                    name="",
                    description="x",
                    system_prompt_prefix="",
                    allowed_tools=None,
                    model_key="sub_agent",
                )
            )

    def test_reset_clears_user_layer(
        self, fresh_registry: AgentRegistry, fake_home: Path
    ) -> None:
        _write_agent(fake_home / ".llm-code" / "agents", "ephemeral")
        fresh_registry.discover()
        assert "ephemeral" in fresh_registry.list_names()
        fresh_registry.reset()
        assert "ephemeral" not in fresh_registry.list_names()
        # Built-ins survive.
        assert "build" in fresh_registry.list_names()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_singleton_returns_same_instance(self) -> None:
        reset_registry()
        a = get_registry()
        b = get_registry()
        assert a is b
        reset_registry()

    def test_reset_returns_fresh_instance(self) -> None:
        reset_registry()
        a = get_registry()
        reset_registry()
        b = get_registry()
        assert a is not b


# ---------------------------------------------------------------------------
# AgentTool integration — schema reflects registry
# ---------------------------------------------------------------------------


class TestAgentToolSchemaIntegration:
    def test_schema_includes_built_ins(self) -> None:
        from llm_code.tools.agent import AgentTool

        reset_registry()
        try:
            tool = AgentTool(runtime_factory=lambda *a, **kw: None)
            schema_enum = tool.input_schema["properties"]["role"]["enum"]
            for builtin in BUILT_IN_ROLES:
                assert builtin in schema_enum
        finally:
            reset_registry()

    def test_schema_includes_custom(
        self, fake_home: Path
    ) -> None:
        from llm_code.tools.agent import AgentTool

        _write_agent(fake_home / ".llm-code" / "agents", "qa-bot")
        reset_registry()
        try:
            get_registry().discover()
            tool = AgentTool(runtime_factory=lambda *a, **kw: None)
            schema_enum = tool.input_schema["properties"]["role"]["enum"]
            assert "qa-bot" in schema_enum
        finally:
            reset_registry()

    def test_unknown_role_lists_valid_options(self) -> None:
        from llm_code.tools.agent import AgentTool

        reset_registry()
        try:
            tool = AgentTool(runtime_factory=lambda *a, **kw: None)
            result = tool.execute({"role": "ghost", "task": "x"})
            assert result.is_error
            assert "Unknown role" in result.output
            assert "build" in result.output
        finally:
            reset_registry()

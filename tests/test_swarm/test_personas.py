"""Tests for built-in agent personas ported from oh-my-opencode."""
from __future__ import annotations

import pytest

from llm_code.swarm.personas import BUILTIN_PERSONAS, AgentPersona


EXPECTED_PERSONAS = {
    "sisyphus",
    "sisyphus-junior",
    "oracle",
    "librarian",
    "atlas",
    "explore",
    "metis",
    "momus",
    "multimodal-looker",
}


class TestBuiltinPersonas:
    def test_all_expected_personas_loaded(self):
        assert EXPECTED_PERSONAS.issubset(set(BUILTIN_PERSONAS))

    @pytest.mark.parametrize("name", sorted(EXPECTED_PERSONAS))
    def test_persona_has_non_empty_system_prompt(self, name):
        persona = BUILTIN_PERSONAS[name]
        assert isinstance(persona, AgentPersona)
        assert persona.system_prompt.strip() != ""
        assert len(persona.system_prompt) > 100

    @pytest.mark.parametrize("name", sorted(EXPECTED_PERSONAS))
    def test_persona_has_description(self, name):
        assert BUILTIN_PERSONAS[name].description.strip() != ""

    @pytest.mark.parametrize("name", sorted(EXPECTED_PERSONAS))
    def test_persona_model_hint_valid(self, name):
        assert BUILTIN_PERSONAS[name].model_hint in {"thinking", "fast", "default"}

    def test_oracle_denies_write_tools(self):
        oracle = BUILTIN_PERSONAS["oracle"]
        assert "write" in oracle.denied_tools
        assert "edit" in oracle.denied_tools

    def test_sisyphus_junior_blocks_delegation(self):
        junior = BUILTIN_PERSONAS["sisyphus-junior"]
        assert "task" in junior.denied_tools
        assert "delegate_task" in junior.denied_tools

    def test_multimodal_looker_only_allows_read(self):
        looker = BUILTIN_PERSONAS["multimodal-looker"]
        assert looker.allowed_tools == ("read",)

    def test_atlas_denies_write(self):
        atlas = BUILTIN_PERSONAS["atlas"]
        assert "write" in atlas.denied_tools

    def test_personas_are_frozen_dataclasses(self):
        persona = BUILTIN_PERSONAS["oracle"]
        with pytest.raises(Exception):
            persona.temperature = 0.9  # type: ignore[misc]


class TestPersonaSpawnIntegration:
    """Integration: SwarmManager.create_member with persona= applies overrides."""

    @pytest.mark.asyncio
    async def test_create_member_with_persona_prepends_prompt(self, tmp_path, monkeypatch):
        from llm_code.runtime.config import RuntimeConfig
        from llm_code.swarm.manager import SwarmManager

        async def fake_spawn(self, member_id, role, task, model):  # noqa: ARG001
            # Capture the task that was sent to the backend
            fake_spawn.captured_task = task
            return 1234

        monkeypatch.setattr(
            "llm_code.swarm.backend_subprocess.SubprocessBackend.spawn",
            fake_spawn,
        )

        config = RuntimeConfig()
        manager = SwarmManager(
            swarm_dir=tmp_path,
            max_members=5,
            backend_preference="subprocess",
            config=config,
        )

        member = await manager.create_member(
            role="researcher",
            task="Find caching patterns",
            backend="subprocess",
            persona="oracle",
        )

        assert member.id
        assert "strategic technical advisor" in fake_spawn.captured_task
        assert "Find caching patterns" in fake_spawn.captured_task

    @pytest.mark.asyncio
    async def test_unknown_persona_raises(self, tmp_path):
        from llm_code.runtime.config import RuntimeConfig
        from llm_code.swarm.manager import SwarmManager

        manager = SwarmManager(
            swarm_dir=tmp_path,
            max_members=5,
            backend_preference="subprocess",
            config=RuntimeConfig(),
        )

        with pytest.raises(ValueError, match="Unknown persona"):
            await manager.create_member(
                role="x",
                task="y",
                backend="subprocess",
                persona="nonexistent",
            )

"""Tests for team template data model and persistence."""
from __future__ import annotations

import json

import pytest

from llm_code.swarm.team import TeamMemberTemplate, TeamTemplate, save_team, load_team, list_teams


class TestTeamMemberTemplate:
    def test_create_minimal(self) -> None:
        m = TeamMemberTemplate(role="reviewer")
        assert m.role == "reviewer"
        assert m.model == ""
        assert m.backend == ""
        assert m.system_prompt == ""

    def test_create_full(self) -> None:
        m = TeamMemberTemplate(role="coder", model="sonnet", backend="worktree", system_prompt="Write code")
        assert m.model == "sonnet"


class TestTeamTemplate:
    def test_create(self) -> None:
        t = TeamTemplate(
            name="test-team",
            description="A test team",
            members=(TeamMemberTemplate(role="a"),),
        )
        assert t.name == "test-team"
        assert len(t.members) == 1
        assert t.max_timeout == 600

    def test_frozen(self) -> None:
        t = TeamTemplate(name="x", description="d", members=())
        with pytest.raises(AttributeError):
            t.name = "y"


class TestTeamPersistence:
    def test_save_and_load(self, tmp_path) -> None:
        team = TeamTemplate(
            name="review-team",
            description="Code review",
            members=(
                TeamMemberTemplate(role="security", model="sonnet"),
                TeamMemberTemplate(role="quality", model="haiku"),
            ),
            coordinator_model="sonnet",
            max_timeout=300,
        )
        save_team(team, tmp_path)
        loaded = load_team("review-team", tmp_path)
        assert loaded.name == "review-team"
        assert len(loaded.members) == 2
        assert loaded.members[0].role == "security"
        assert loaded.members[0].model == "sonnet"
        assert loaded.coordinator_model == "sonnet"
        assert loaded.max_timeout == 300

    def test_load_nonexistent_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_team("nonexistent", tmp_path)

    def test_list_teams_empty(self, tmp_path) -> None:
        assert list_teams(tmp_path) == []

    def test_list_teams(self, tmp_path) -> None:
        for name in ("alpha", "beta"):
            save_team(TeamTemplate(name=name, description="d", members=()), tmp_path)
        names = list_teams(tmp_path)
        assert set(names) == {"alpha", "beta"}

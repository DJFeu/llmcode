"""Tests for Markdown frontmatter agent loader."""
from __future__ import annotations

from pathlib import Path

from llm_code.tools.agent_loader import (
    _frontmatter_to_role,
    _load_agents_from_dir,
    _parse_frontmatter,
    load_all_agents,
)
from llm_code.tools.agent_roles import BUILT_IN_ROLES


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_simple_scalar(self) -> None:
        text = "---\nname: my-agent\ndescription: A test agent\n---\nBody text here"
        data, body = _parse_frontmatter(text)
        assert data["name"] == "my-agent"
        assert data["description"] == "A test agent"
        assert body == "Body text here"

    def test_list_values(self) -> None:
        text = "---\nname: x\ntools:\n  - read_file\n  - bash\n---\nBody"
        data, body = _parse_frontmatter(text)
        assert data["tools"] == ["read_file", "bash"]

    def test_no_frontmatter(self) -> None:
        text = "Just plain text"
        data, body = _parse_frontmatter(text)
        assert data == {}
        assert body == "Just plain text"

    def test_empty_body(self) -> None:
        text = "---\nname: minimal\n---\n"
        data, body = _parse_frontmatter(text)
        assert data["name"] == "minimal"
        assert body == ""

    def test_comments_ignored(self) -> None:
        text = "---\nname: test\n# comment\ndescription: desc\n---\nBody"
        data, body = _parse_frontmatter(text)
        assert "name" in data
        assert "description" in data

    def test_mixed_scalar_and_list(self) -> None:
        text = (
            "---\nname: mixed\nmodel: primary\ntools:\n  - bash\n  - read_file\n"
            "disallowed_tools:\n  - write_file\n---\nPrompt text"
        )
        data, body = _parse_frontmatter(text)
        assert data["model"] == "primary"
        assert data["tools"] == ["bash", "read_file"]
        assert data["disallowed_tools"] == ["write_file"]


# ---------------------------------------------------------------------------
# _frontmatter_to_role
# ---------------------------------------------------------------------------

class TestFrontmatterToRole:
    def test_valid_role(self) -> None:
        data = {"name": "reviewer", "description": "Code reviewer"}
        role = _frontmatter_to_role(data, "Review code carefully.", "test.md")
        assert role is not None
        assert role.name == "reviewer"
        assert role.description == "Code reviewer"
        assert role.system_prompt_prefix == "Review code carefully."
        assert role.is_builtin is False

    def test_missing_name_returns_none(self) -> None:
        data = {"description": "no name"}
        assert _frontmatter_to_role(data, "body", "test.md") is None

    def test_tools_as_list(self) -> None:
        data = {"name": "x", "tools": ["bash", "read_file"]}
        role = _frontmatter_to_role(data, "", "test.md")
        assert role is not None
        assert role.allowed_tools == frozenset({"bash", "read_file"})

    def test_tools_wildcard(self) -> None:
        data = {"name": "x", "tools": "*"}
        role = _frontmatter_to_role(data, "", "test.md")
        assert role is not None
        assert role.allowed_tools is None

    def test_disallowed_tools(self) -> None:
        data = {"name": "x", "disallowed_tools": ["write_file"]}
        role = _frontmatter_to_role(data, "", "test.md")
        assert role is not None
        assert role.disallowed_tools == frozenset({"write_file"})

    def test_defaults(self) -> None:
        data = {"name": "minimal"}
        role = _frontmatter_to_role(data, "", "test.md")
        assert role is not None
        assert role.model_key == "sub_agent"
        assert role.is_async is False
        assert role.disallowed_tools is None


# ---------------------------------------------------------------------------
# _load_agents_from_dir
# ---------------------------------------------------------------------------

class TestLoadAgentsFromDir:
    def test_loads_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "auditor.md").write_text(
            "---\nname: auditor\ndescription: Audits code\n---\nAudit prompt"
        )
        (tmp_path / "helper.md").write_text(
            "---\nname: helper\n---\nHelper prompt"
        )
        agents = _load_agents_from_dir(tmp_path)
        assert "auditor" in agents
        assert "helper" in agents
        assert agents["auditor"].system_prompt_prefix == "Audit prompt"

    def test_ignores_non_md(self, tmp_path: Path) -> None:
        (tmp_path / "readme.txt").write_text("not an agent")
        agents = _load_agents_from_dir(tmp_path)
        assert len(agents) == 0

    def test_skips_invalid_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("no frontmatter here")
        agents = _load_agents_from_dir(tmp_path)
        # No crash, but no name → skipped
        assert len(agents) == 0

    def test_nonexistent_dir(self) -> None:
        agents = _load_agents_from_dir(Path("/nonexistent/dir"))
        assert agents == {}


# ---------------------------------------------------------------------------
# load_all_agents — cascade
# ---------------------------------------------------------------------------

class TestLoadAllAgents:
    def test_includes_builtins(self) -> None:
        agents = load_all_agents(project_path=None)
        for name in BUILT_IN_ROLES:
            assert name in agents

    def test_project_agents_shadow_builtins(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".llm-code" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "explore.md").write_text(
            "---\nname: explore\ndescription: Custom explore\n---\nCustom"
        )
        agents = load_all_agents(project_path=tmp_path)
        assert agents["explore"].description == "Custom explore"
        assert agents["explore"].is_builtin is False

    def test_builtins_preserved_when_no_project(self) -> None:
        agents = load_all_agents(project_path=None)
        assert agents["build"].is_builtin is True
        assert agents["plan"].is_builtin is True

"""Integration tests for v3 features: skills, streaming, plugins, prompt."""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from llm_code.cli.streaming import IncrementalMarkdownRenderer
from llm_code.marketplace.installer import PluginInstaller
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.skills import Skill, SkillLoader, SkillSet


def test_plugin_with_skills_loaded(tmp_path):
    """Plugin with skills/ dir → skills loaded."""
    plugin_dir = tmp_path / "plugins" / "my-plugin"
    plugin_dir.mkdir(parents=True)
    meta = plugin_dir / ".claude-plugin"
    meta.mkdir()
    (meta / "plugin.json").write_text(json.dumps({
        "name": "my-plugin", "version": "1.0.0", "description": "test", "skills": "./skills/",
    }))
    skills_dir = plugin_dir / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: plugin-skill\ndescription: From plugin\nauto: true\ntrigger: ps\n---\nDo plugin thing.\n"
    )
    installer = PluginInstaller(tmp_path / "plugins")
    installed = installer.list_installed()
    assert len(installed) == 1
    plugin = installed[0]
    skill_path = plugin.path / plugin.manifest.skills
    loader = SkillLoader()
    skill_set = loader.load_from_dirs([skill_path])
    assert len(skill_set.auto_skills) == 1
    assert skill_set.auto_skills[0].name == "plugin-skill"


def test_skills_in_prompt():
    """Routed skills appear in system prompt, command skills don't."""
    builder = SystemPromptBuilder()
    ctx = ProjectContext(cwd=Path("/tmp"), is_git_repo=False, git_status="", instructions="")
    auto_skill = Skill(name="auto-one", description="auto", content="AUTO CONTENT", auto=True, trigger="a")
    cmd_skill = Skill(name="cmd-one", description="cmd", content="CMD CONTENT", auto=False, trigger="c")
    skills = SkillSet(
        auto_skills=(auto_skill,),
        command_skills=(cmd_skill,),
    )
    # With routed_skills, only those skills are injected
    prompt = builder.build(ctx, skills=skills, routed_skills=(auto_skill,))
    assert "AUTO CONTENT" in prompt
    assert "CMD CONTENT" not in prompt
    assert "CACHE BOUNDARY" in prompt
    # Without routed_skills, no auto-skills appear
    prompt_empty = builder.build(ctx, skills=skills)
    assert "AUTO CONTENT" not in prompt_empty


def test_prefix_cache_order():
    """Static content before cache boundary, dynamic after."""
    builder = SystemPromptBuilder()
    ctx = ProjectContext(cwd=Path("/tmp"), is_git_repo=True, git_status="M x.py", instructions="Rule 1")
    prompt = builder.build(ctx)
    boundary = prompt.find("CACHE BOUNDARY")
    git_pos = prompt.find("M x.py")
    intro_pos = prompt.find("coding assistant")
    assert intro_pos < boundary < git_pos


def test_streaming_markdown_full_flow():
    """Token-by-token streaming of mixed Markdown."""
    console = Console(file=StringIO(), force_terminal=False)
    renderer = IncrementalMarkdownRenderer(console)
    text = "# Hello\n\nThis is a test.\n\n```python\nx = 1\n```\n\nDone."
    for ch in text:
        renderer.feed(ch)
    renderer.finish()
    output = console.file.getvalue()
    assert "Hello" in output
    assert "x = 1" in output
    assert "Done" in output

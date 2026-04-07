"""SkillLoadTool — let the LLM actively load a skill into the current turn.

Complements the auto-router (which pre-injects matched skills): the model can
discover what skills exist via the tool description and choose to load any of
them on demand.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class SkillLoadInput(BaseModel):
    name: str


class SkillLoadTool(Tool):
    """Load a skill by name and inject its full content as the tool result.

    The LLM sees a list of available skills in this tool's description.
    When the model decides one matches the current task, it calls
    skill_load(name=...) and gets back the skill's full content + a list
    of files in the skill directory (scripts, templates, references).
    """

    def __init__(self, skills: Any) -> None:
        """skills: SkillSet instance with auto_skills + command_skills tuples."""
        self._skills = skills

    @property
    def name(self) -> str:
        return "skill_load"

    @property
    def description(self) -> str:
        if self._skills is None:
            return "Load a specialized skill. No skills are currently available."

        all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)
        if not all_skills:
            return "Load a specialized skill. No skills are currently available."

        lines = [
            "Load a specialized skill that provides domain-specific instructions and workflows.",
            "",
            "When the user's task matches one of the skills below, call this tool to load the full skill content into context.",
            "",
            "Available skills:",
        ]
        for skill in sorted(all_skills, key=lambda s: s.name):
            desc_short = skill.description[:120] if skill.description else ""
            lines.append(f"- **{skill.name}**: {desc_short}")
        return "\n".join(lines)

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to load (must match an available skill)",
                },
            },
            "required": ["name"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[SkillLoadInput]:
        return SkillLoadInput

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def execute(self, args: dict) -> ToolResult:
        name = args["name"].strip()
        if not self._skills:
            return ToolResult(output=f"No skills available. Cannot load: {name}", is_error=True)

        all_skills = list(self._skills.auto_skills) + list(self._skills.command_skills)
        target = next((s for s in all_skills if s.name == name), None)
        if target is None:
            available = ", ".join(sorted(s.name for s in all_skills)) or "none"
            return ToolResult(
                output=f"Skill '{name}' not found. Available: {available}",
                is_error=True,
            )

        # Try to find sibling files in the skill's directory (scripts, templates)
        skill_dir = self._find_skill_dir(target)
        files_section = ""
        if skill_dir is not None:
            sibling_files = []
            try:
                for entry in sorted(skill_dir.rglob("*")):
                    if entry.is_file() and entry.name != "SKILL.md":
                        sibling_files.append(str(entry.relative_to(skill_dir)))
                        if len(sibling_files) >= 20:
                            break
            except OSError:
                pass
            if sibling_files:
                files_list = "\n".join(f"  - {f}" for f in sibling_files)
                files_section = (
                    f"\n\nResource files in skill directory ({skill_dir}):\n{files_list}\n"
                    f"You can read these with the read tool using paths relative to {skill_dir}."
                )

        output = (
            f"<skill_content name=\"{target.name}\">\n"
            f"# Skill: {target.name}\n\n"
            f"{target.content.strip()}"
            f"{files_section}\n"
            f"</skill_content>"
        )
        return ToolResult(
            output=output,
            metadata={"skill_name": target.name, "skill_dir": str(skill_dir) if skill_dir else ""},
        )

    @staticmethod
    def _find_skill_dir(skill: Any) -> Path | None:
        """Try to locate the directory the skill was loaded from.

        Returns None if the skill object doesn't carry path metadata.
        """
        path_attr = getattr(skill, "path", None) or getattr(skill, "source_path", None)
        if path_attr:
            p = Path(str(path_attr))
            if p.is_file():
                return p.parent
            if p.is_dir():
                return p
        return None

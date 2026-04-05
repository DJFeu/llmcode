"""Skills system: load and classify SKILL.md files into SkillSet."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


@dataclass(frozen=True)
class SkillDependency:
    """A dependency on another skill."""

    name: str
    registry: str = ""  # empty = search all registries


@dataclass(frozen=True)
class Skill:
    """A single skill loaded from a SKILL.md file."""

    name: str
    description: str
    content: str
    auto: bool = False
    trigger: str = ""
    version: str = ""
    tags: tuple[str, ...] = ()
    model: str = ""
    depends: tuple[SkillDependency, ...] = ()
    min_version: str = ""

    def __post_init__(self) -> None:
        # If trigger not set (empty string), default it to name.
        # Because frozen=True we must use object.__setattr__.
        if not self.trigger:
            object.__setattr__(self, "trigger", self.name)


@dataclass(frozen=True)
class SkillSet:
    """Container for classified skills."""

    auto_skills: tuple[Skill, ...]
    command_skills: tuple[Skill, ...]


class SkillLoader:
    """Loads skills from directories."""

    @staticmethod
    def load_skill(path: Path) -> Skill:
        """Parse a SKILL.md file and return a Skill."""
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError(f"Invalid SKILL.md format: {path}")

        frontmatter_raw, content = m.group(1), m.group(2)

        # Simple key:value YAML parser
        meta: dict[str, str] = {}
        for line in frontmatter_raw.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")

        name = meta.get("name", "")
        description = meta.get("description", "")
        auto_raw = meta.get("auto", "false").lower()
        auto = auto_raw in ("true", "yes", "1")
        trigger = meta.get("trigger", "")  # default handled in __post_init__

        return Skill(
            name=name,
            description=description,
            content=content,
            auto=auto,
            trigger=trigger,
        )

    @staticmethod
    def load_from_dirs(dirs: list[Path]) -> SkillSet:
        """Scan each directory for subdirs containing SKILL.md and classify."""
        auto: list[Skill] = []
        command: list[Skill] = []

        for directory in dirs:
            if not directory.is_dir():
                continue
            for subdir in sorted(directory.iterdir()):
                if not subdir.is_dir():
                    continue
                skill_md = subdir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                if (subdir / ".disabled").exists():
                    continue
                skill = SkillLoader.load_skill(skill_md)
                if skill.auto:
                    auto.append(skill)
                else:
                    command.append(skill)

        return SkillSet(
            auto_skills=tuple(auto),
            command_skills=tuple(command),
        )

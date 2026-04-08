"""Skills system: load and classify SKILL.md files into SkillSet."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

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
    keywords: tuple[str, ...] = ()
    model: str = ""
    depends: tuple[SkillDependency, ...] = ()
    min_version: str = ""
    # Frontmatter hooks: mapping event name -> builtin hook callable name.
    # e.g. {"pre_tool_use": "auto_format", "post_tool_use": "auto_lint"}
    hooks: tuple[tuple[str, str], ...] = ()
    # Dynamic slash commands declared in frontmatter.
    # Each entry is a dict with keys: name, description, argument_hint (optional).
    commands: tuple[dict, ...] = ()
    # Names of MCP servers (from RuntimeConfig.mcp.on_demand) this skill
    # requires. Spawned at ConversationRuntime init under a skill-scoped
    # owner id; auto-torn-down at session end via stop_all.
    mcp_servers: tuple[str, ...] = ()

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

        try:
            meta = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError:
            meta = {}

        name = str(meta.get("name", ""))
        description = str(meta.get("description", ""))
        auto_raw = meta.get("auto", False)
        auto = auto_raw is True or str(auto_raw).lower() in ("true", "yes", "1")

        # Heuristic: detect auto skills from description keywords
        # Superpowers skills use "Use when..." or "MUST use this before..." patterns
        if not auto and description:
            _desc_lower = description.lower()
            _auto_patterns = (
                "use when starting any",
                "must use this before",
                "you must use this",
                "use this skill when",
                "automatically activate",
            )
            if any(p in _desc_lower for p in _auto_patterns):
                auto = True
        trigger = str(meta.get("trigger", ""))

        version = str(meta.get("version", ""))
        model = str(meta.get("model", ""))
        min_version = str(meta.get("min_version", ""))

        tags_raw = meta.get("tags", [])
        tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()

        keywords_raw = meta.get("keywords", [])
        keywords = tuple(str(k).lower() for k in keywords_raw) if isinstance(keywords_raw, list) else ()

        depends_raw = meta.get("depends", [])
        depends: tuple[SkillDependency, ...] = ()
        if isinstance(depends_raw, list):
            deps = []
            for item in depends_raw:
                if isinstance(item, dict) and "name" in item:
                    deps.append(SkillDependency(
                        name=str(item["name"]),
                        registry=str(item.get("registry", "")),
                    ))
            depends = tuple(deps)

        commands_raw = meta.get("commands", [])
        commands: tuple[dict, ...] = ()
        if isinstance(commands_raw, list):
            parsed_cmds: list[dict] = []
            for item in commands_raw:
                if not isinstance(item, dict) or "name" not in item:
                    continue
                parsed_cmds.append({
                    "name": str(item["name"]),
                    "description": str(item.get("description", "")),
                    "argument_hint": str(item.get("argument_hint", "")),
                })
            commands = tuple(parsed_cmds)

        mcp_servers_raw = meta.get("mcp_servers", [])
        mcp_servers: tuple[str, ...] = ()
        if isinstance(mcp_servers_raw, list):
            mcp_servers = tuple(
                str(item) for item in mcp_servers_raw if isinstance(item, (str, int))
            )

        hooks_raw = meta.get("hooks", {})
        hooks: tuple[tuple[str, str], ...] = ()
        if isinstance(hooks_raw, dict):
            hooks = tuple(
                (str(event), str(handler))
                for event, handler in hooks_raw.items()
                if isinstance(handler, str) and handler
            )

        return Skill(
            name=name,
            description=description,
            content=content,
            auto=auto,
            trigger=trigger,
            version=version,
            tags=tags,
            keywords=keywords,
            model=model,
            depends=depends,
            min_version=min_version,
            hooks=hooks,
            commands=commands,
            mcp_servers=mcp_servers,
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

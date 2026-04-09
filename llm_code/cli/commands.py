"""Slash command registry — single source of truth."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    """Definition of a single slash command."""

    name: str
    description: str
    no_arg: bool = False  # True = execute immediately on selection


# Canonical command list — all other files import from here.
COMMAND_REGISTRY: tuple[CommandDef, ...] = (
    CommandDef("help", "Show help", no_arg=True),
    CommandDef("clear", "Clear conversation", no_arg=True),
    CommandDef("model", "Switch model"),
    CommandDef("cost", "Token usage", no_arg=True),
    CommandDef("budget", "Set token budget"),
    CommandDef("undo", "Undo last change"),
    CommandDef("cd", "Change directory"),
    CommandDef("config", "Runtime config", no_arg=True),
    CommandDef("settings", "Open settings panel", no_arg=True),
    CommandDef("set", "Set config value: /set temperature 0.5"),
    CommandDef("thinking", "Toggle thinking"),
    CommandDef("vim", "Toggle vim mode", no_arg=True),
    CommandDef("image", "Attach image"),
    CommandDef("search", "Search history"),
    CommandDef("index", "Project index"),
    CommandDef("session", "Sessions"),
    CommandDef("skill", "Browse skills", no_arg=True),
    CommandDef("plugin", "Browse plugins", no_arg=True),
    CommandDef("mcp", "MCP servers", no_arg=True),
    CommandDef("memory", "Project memory"),
    CommandDef("cron", "Scheduled tasks"),
    CommandDef("task", "Task lifecycle"),
    CommandDef("swarm", "Swarm coordination"),
    CommandDef("orchestrate", "Run orchestrator with category routing + retry"),
    CommandDef("voice", "Voice input"),
    CommandDef("ide", "IDE bridge"),
    CommandDef("vcr", "VCR recording"),
    CommandDef("checkpoint", "Checkpoints"),
    CommandDef("diff", "Diff since checkpoint"),
    CommandDef("hida", "HIDA classification", no_arg=True),
    CommandDef("lsp", "LSP status", no_arg=True),
    CommandDef("cancel", "Cancel generation", no_arg=True),
    CommandDef("plan", "Plan/Act mode"),
    CommandDef("mode", "Switch mode (suggest/normal/plan)"),
    CommandDef("analyze", "Code analysis"),
    CommandDef("diff_check", "Diff analysis"),
    CommandDef("dump", "Dump context"),
    CommandDef("map", "Repo map"),
    CommandDef("harness", "Harness controls"),
    CommandDef("knowledge", "Knowledge base"),
    CommandDef("gain", "Token savings report", no_arg=True),
    CommandDef("profile", "Per-model token/cost breakdown", no_arg=True),
    CommandDef("init", "Generate AGENTS.md from repo analysis"),
    CommandDef("yolo", "Toggle YOLO mode (auto-accept all permissions)", no_arg=True),
    CommandDef("copy", "Copy last response to clipboard", no_arg=True),
    CommandDef("compact", "Compact conversation to free context window"),
    CommandDef("export", "Export conversation to markdown file"),
    CommandDef("exit", "Quit", no_arg=True),
    CommandDef("quit", "Quit", no_arg=True),
)

# Derived sets for backward compatibility
KNOWN_COMMANDS: frozenset[str] = frozenset(c.name for c in COMMAND_REGISTRY)


# Skill-declared dynamic commands. Registered at skill-load time via
# ``register_skill_commands``. Name collisions with the static registry are
# resolved by prefixing the skill name: ``<skill>/<command>``.
SKILL_COMMANDS: dict[str, CommandDef] = {}


def register_skill_commands(skill, registry: dict[str, CommandDef] | None = None) -> list[str]:
    """Register a skill's declared commands into *registry* (defaults to SKILL_COMMANDS).

    Returns the list of command names that were actually registered.
    """
    target = registry if registry is not None else SKILL_COMMANDS
    registered: list[str] = []
    for cmd in getattr(skill, "commands", ()) or ():
        name = str(cmd.get("name", "")).strip()
        if not name:
            continue
        description = str(cmd.get("description", ""))
        hint = str(cmd.get("argument_hint", ""))
        no_arg = not hint
        # Collision: prefix with skill name
        effective_name = name
        if effective_name in KNOWN_COMMANDS or effective_name in target:
            effective_name = f"{skill.name}/{name}"
        if effective_name in target:
            # still colliding (duplicate skill+name) — skip
            continue
        target[effective_name] = CommandDef(
            name=effective_name,
            description=description,
            no_arg=no_arg,
        )
        registered.append(effective_name)
    return registered


def all_known_commands(include_skills: bool = True) -> frozenset[str]:
    """All known slash commands — static registry plus optionally skill-declared."""
    if not include_skills:
        return KNOWN_COMMANDS
    return KNOWN_COMMANDS | frozenset(SKILL_COMMANDS.keys())


@dataclass(frozen=True)
class SlashCommand:
    name: str
    args: str


def parse_slash_command(text: str) -> SlashCommand | None:
    """Parse a slash command from text.

    Returns a SlashCommand if the text starts with '/', otherwise None.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    # Strip leading slash
    rest = stripped[1:]

    # Split on first whitespace
    parts = rest.split(None, 1)
    if not parts:
        return None

    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    return SlashCommand(name=name, args=args)

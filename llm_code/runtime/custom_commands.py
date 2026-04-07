"""Template-based custom slash commands.

Discovers user-defined commands from:
  - <project>/.llmcode/commands/*.md  (project-scoped)
  - ~/.llmcode/commands/*.md           (user-global)

File format:
  ---
  description: Run a code review on the current diff
  ---
  Review the changes in `git diff` and check for:
  - bugs
  - security issues
  - style violations

  $ARGUMENTS

When the user types `/<name> [args]`, the file content is loaded, $ARGUMENTS
is replaced with the user input, and the result is sent to the LLM as a
turn prompt — exactly like Claude Code's slash command templates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
# Slugs: lowercase letters, digits, hyphens, underscores. Length 1-32.
_VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class CustomCommand:
    """A slash command defined by a markdown template file."""

    name: str
    description: str
    template: str
    source: Path

    def render(self, args: str) -> str:
        """Substitute $ARGUMENTS in the template with the user's args."""
        return self.template.replace("$ARGUMENTS", args.strip() or "(none)")


def _parse_command_file(path: Path) -> CustomCommand | None:
    """Parse a command markdown file. Returns None if invalid."""
    name = path.stem
    if not _VALID_NAME_RE.match(name):
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    description = ""
    template = raw

    m = _FRONTMATTER_RE.match(raw)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
            if isinstance(meta, dict):
                description = str(meta.get("description", "")).strip()
        except yaml.YAMLError:
            pass
        template = m.group(2)

    template = template.strip()
    if not template:
        return None

    return CustomCommand(
        name=name,
        description=description or f"Custom command from {path.name}",
        template=template,
        source=path,
    )


_BUILTIN_COMMANDS_DIR = Path(__file__).parent / "builtin_commands"


def discover_custom_commands(cwd: Path) -> dict[str, CustomCommand]:
    """Find all custom commands from built-in + user-global + project directories.

    Precedence (lowest to highest): builtin → user-global → project.
    Project commands take precedence over user-global, which take precedence
    over the built-in commands shipped with llm-code.
    """
    user_dir = Path.home() / ".llmcode" / "commands"
    project_dir = cwd / ".llmcode" / "commands"

    commands: dict[str, CustomCommand] = {}

    # Built-in commands (lowest priority — shipped with llm-code)
    if _BUILTIN_COMMANDS_DIR.is_dir():
        for path in sorted(_BUILTIN_COMMANDS_DIR.glob("*.md")):
            cmd = _parse_command_file(path)
            if cmd is not None:
                commands[cmd.name] = cmd

    # User-global (overrides built-in)
    if user_dir.is_dir():
        for path in sorted(user_dir.glob("*.md")):
            cmd = _parse_command_file(path)
            if cmd is not None:
                commands[cmd.name] = cmd

    # Project-level (overrides everything)
    if project_dir.is_dir():
        for path in sorted(project_dir.glob("*.md")):
            cmd = _parse_command_file(path)
            if cmd is not None:
                commands[cmd.name] = cmd

    return commands


__all__ = ["CustomCommand", "discover_custom_commands"]

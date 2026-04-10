"""Load agent definitions from Markdown frontmatter files.

Scans three directories in priority order (later entries shadow earlier):

    1. Built-in roles (hardcoded in ``agent_roles.py``)
    2. User agents:    ``~/.llm-code/agents/*.md``
    3. Project agents: ``.llm-code/agents/*.md``

Each ``.md`` file is a self-contained agent definition:

    ---
    name: security-auditor
    description: Security-focused code reviewer
    tools:
      - read_file
      - grep_search
      - bash
    disallowed_tools:
      - write_file
    model: sub_agent
    max_turns: 50
    memory: project
    ---

    You are a security auditor. Analyze code for OWASP Top 10...

The YAML frontmatter maps to ``AgentRole`` fields.  The body after
``---`` becomes the ``system_prompt_prefix``.

Risk mitigations:
    - User-defined agents get ``is_builtin=False`` which triggers
      Stage 4 filtering (``CUSTOM_AGENT_DISALLOWED`` blocks ``agent``
      tool and ``swarm_create/delete``).
    - Invalid YAML or missing ``name`` silently skips the file with
      a warning (no crash).
    - ``_parse_frontmatter()`` is a pure function for easy testing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from llm_code.tools.agent_roles import BUILT_IN_ROLES, AgentRole

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into YAML frontmatter dict and body text.

    Returns ``({}, full_text)`` if no valid frontmatter is found.
    Uses a minimal parser to avoid a hard dependency on PyYAML.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text

    # Find closing ---
    rest = stripped[3:]
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return {}, text

    yaml_block = rest[:end_idx].strip()
    body = rest[end_idx + 4:].strip()  # skip \n---

    # Minimal YAML-subset parser (flat keys, string/list values)
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in yaml_block.split("\n"):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue

        # List item under current key
        if stripped_line.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
                data[current_key] = current_list
            current_list.append(stripped_line[2:].strip())
            continue

        # Key: value pair
        if ":" in stripped_line:
            # Flush previous list
            current_list = None

            colon_idx = stripped_line.index(":")
            key = stripped_line[:colon_idx].strip()
            value = stripped_line[colon_idx + 1:].strip()

            current_key = key
            if value:
                # Scalar value
                data[key] = value
            # If no value, next lines might be list items

    return data, body


def _frontmatter_to_role(data: dict[str, Any], body: str, source: str) -> AgentRole | None:
    """Convert parsed frontmatter + body into an AgentRole.

    Returns None if required fields are missing.
    """
    name = data.get("name")
    if not name:
        logger.warning("Agent file %s: missing 'name' in frontmatter, skipping", source)
        return None

    description = data.get("description", f"Custom agent: {name}")

    # Tools
    raw_tools = data.get("tools")
    if isinstance(raw_tools, list):
        allowed_tools: frozenset[str] | None = frozenset(raw_tools)
    elif raw_tools == "*" or raw_tools is None:
        allowed_tools = None
    else:
        allowed_tools = None

    raw_disallowed = data.get("disallowed_tools")
    disallowed_tools: frozenset[str] | None = None
    if isinstance(raw_disallowed, list):
        disallowed_tools = frozenset(raw_disallowed)

    model_key = data.get("model", "sub_agent")
    is_async = str(data.get("is_async", "false")).lower() == "true"

    return AgentRole(
        name=str(name),
        description=str(description),
        system_prompt_prefix=body,
        allowed_tools=allowed_tools,
        model_key=str(model_key),
        disallowed_tools=disallowed_tools,
        is_builtin=False,
        is_async=is_async,
    )


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def _load_agents_from_dir(directory: Path) -> dict[str, AgentRole]:
    """Load all .md agent definitions from a directory."""
    agents: dict[str, AgentRole] = {}
    if not directory.is_dir():
        return agents

    for md_file in sorted(directory.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot read agent file %s: %s", md_file, e)
            continue

        data, body = _parse_frontmatter(text)
        role = _frontmatter_to_role(data, body, str(md_file))
        if role is not None:
            agents[role.name] = role

    return agents


def load_all_agents(
    project_path: Path | None = None,
) -> dict[str, AgentRole]:
    """Load agents with cascade: built-in → user → project.

    Later sources shadow earlier ones (project overrides user overrides built-in).
    """
    # Layer 1: built-in
    agents: dict[str, AgentRole] = dict(BUILT_IN_ROLES)

    # Layer 2: user-global
    user_dir = Path.home() / ".llm-code" / "agents"
    agents.update(_load_agents_from_dir(user_dir))

    # Layer 3: project-local
    if project_path is not None:
        project_dir = project_path / ".llm-code" / "agents"
        agents.update(_load_agents_from_dir(project_dir))

    return agents

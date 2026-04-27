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

    v16 M7 — uses PyYAML when available so nested constructs
    (``mcp_servers:`` arrays of dicts, list-of-strings tool entries)
    parse cleanly. Falls back to the previous flat-string parser when
    PyYAML is unavailable so deployment paths that don't ship YAML
    keep working with the wave-1 frontmatter shape.
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

    # Prefer PyYAML for proper YAML support (nested dicts, lists of
    # dicts, etc.). Falls through to the legacy flat parser when the
    # block isn't valid YAML so wave-1 frontmatter stays compatible.
    try:
        import yaml as _yaml

        parsed = _yaml.safe_load(yaml_block)
        if isinstance(parsed, dict):
            return parsed, body
    except Exception:  # noqa: BLE001 — fall through to flat parser
        pass

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

    v16 M7 — picks up three new frontmatter fields:

    * ``tools`` — list entries may include wildcards (``read_*``) and
      args allowlists (``bash:git status,git diff``). Stored verbatim
      on ``AgentRole.tool_specs`` so the subagent factory can run
      :func:`runtime.tool_policy.resolve_tool_subset` against the
      parent's full tool surface at spawn time.
    * ``tool_policy`` — built-in policy name (``read-only`` / ``build``
      / ``verify`` / ``unrestricted``).
    * ``mcp_servers`` — list of inline MCP servers (each dict-shaped
      with ``name`` + ``command`` + optional ``args``).
    """
    name = data.get("name")
    if not name:
        logger.warning("Agent file %s: missing 'name' in frontmatter, skipping", source)
        return None

    description = data.get("description", f"Custom agent: {name}")

    # Tools — wave-1 path stays as-is for plain string lists; M7
    # entries that contain ``*``/``?``/``:`` are kept verbatim on
    # tool_specs so wildcard expansion runs at spawn time.
    raw_tools = data.get("tools")
    tool_specs_list: list[str] = []
    if isinstance(raw_tools, list):
        tool_specs_list = [str(item) for item in raw_tools if isinstance(item, str)]
        # Legacy path: literals-only roles still work via allowed_tools
        # so existing tests + plain whitelists keep their semantics.
        has_dynamic = any(
            ("*" in t) or ("?" in t) or (":" in t) for t in tool_specs_list
        )
        if has_dynamic:
            allowed_tools: frozenset[str] | None = None
        else:
            allowed_tools = frozenset(tool_specs_list)
    elif raw_tools == "*" or raw_tools is None:
        allowed_tools = None
    else:
        allowed_tools = None

    raw_disallowed = data.get("disallowed_tools")
    disallowed_tools: frozenset[str] | None = None
    if isinstance(raw_disallowed, list):
        disallowed_tools = frozenset(raw_disallowed)

    # v16 M7 — tool_policy preset name.
    raw_policy = data.get("tool_policy", "")
    tool_policy = str(raw_policy).strip() if raw_policy else ""

    # v16 M7 — inline MCP servers. Accept either a list of dicts (the
    # documented shape) or skip silently when the value is anything
    # else, so wave-1 frontmatter without the field stays compatible.
    inline_mcp: list[tuple[str, str, tuple[str, ...]]] = []
    raw_mcp = data.get("mcp_servers")
    if isinstance(raw_mcp, list):
        for entry in raw_mcp:
            if not isinstance(entry, dict):
                logger.warning(
                    "Agent file %s: mcp_servers entry must be a mapping; skipping %r",
                    source, entry,
                )
                continue
            mcp_name = entry.get("name")
            mcp_command = entry.get("command")
            mcp_args = entry.get("args") or []
            if not (isinstance(mcp_name, str) and isinstance(mcp_command, str)):
                logger.warning(
                    "Agent file %s: mcp_servers entry needs name+command; skipping %r",
                    source, entry,
                )
                continue
            args_tuple: tuple[str, ...] = tuple(
                str(a) for a in mcp_args if isinstance(a, (str, int, float))
            )
            inline_mcp.append((mcp_name, mcp_command, args_tuple))

    model_key = data.get("model", "sub_agent")
    is_async = str(data.get("is_async", "false")).lower() == "true"

    return AgentRole(
        name=str(name),
        description=str(description),
        system_prompt_prefix=body if isinstance(body, str) else "",
        allowed_tools=allowed_tools,
        model_key=str(model_key),
        disallowed_tools=disallowed_tools,
        is_builtin=False,
        is_async=is_async,
        tool_specs=tuple(tool_specs_list),
        tool_policy=tool_policy,
        inline_mcp_servers=tuple(inline_mcp),
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

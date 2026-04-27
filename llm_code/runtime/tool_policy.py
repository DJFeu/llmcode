"""Per-agent tool policy + wildcard expansion (v16 M7).

Subagent frontmatter under ``.llmcode/agents/<role>.md`` may declare
expressive tool policies that aren't a flat whitelist:

* Wildcards: ``read_*`` matches every tool starting with ``read_``.
* Per-tool args allowlist: ``bash:git status,git diff`` allows ``bash``
  but only when its ``command`` argument has one of the listed prefixes.
* Prebuilt policies: ``read-only`` / ``build`` / ``verify`` /
  ``unrestricted`` map to wildcard expansions defined in this module.

This module is import-cheap — zero deps beyond stdlib — so the agent
loader can pull it in eagerly. The actual subprocess lifecycle for
inline MCP servers lives in :mod:`subagent_factory` (M7 wiring point).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Mapping

# ---------------------------------------------------------------------------
# Prebuilt policies
# ---------------------------------------------------------------------------


# Tool name globs grouped by policy. Each entry expands at agent
# spawn time against the parent's full tool inventory; policies can
# overlap (build extends read-only, etc.). The order in each tuple
# is informational — duplicates collapse via the set semantics below.
BUILTIN_POLICIES: Mapping[str, tuple[str, ...]] = {
    # Read-only: every read tool, search tool, and web tool. No
    # mutation, no shell, no edit. Useful for explore/researcher
    # roles.
    "read-only": (
        "read_*",
        "grep_*",
        "glob_*",
        "ls_*",
        "git_status",
        "git_diff",
        "git_log",
        "lsp_*",
        "web_search",
        "web_fetch",
        "memory_read",
        "memory_list",
    ),
    # Build: read-only + edit + write + bash + memory write.
    "build": (
        "read_*",
        "grep_*",
        "glob_*",
        "ls_*",
        "git_*",
        "edit_*",
        "write_*",
        "multi_edit",
        "bash",
        "lsp_*",
        "web_search",
        "web_fetch",
        "memory_*",
    ),
    # Verify: read tools + bash (for running tests / lints) but no
    # edit/write surface. Adversarial reviewers / CI agents.
    "verify": (
        "read_*",
        "grep_*",
        "glob_*",
        "ls_*",
        "git_status",
        "git_diff",
        "git_log",
        "lsp_*",
        "bash",
        "memory_read",
        "memory_list",
    ),
    # Unrestricted: matches every tool. Equivalent to leaving tools
    # unset on the role definition; named for ergonomic frontmatter.
    "unrestricted": ("*",),
}


# ---------------------------------------------------------------------------
# Tool spec: name + optional args allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Parsed entry from frontmatter ``tools:`` list.

    ``name`` may be a literal (``bash``) or a glob (``read_*``);
    ``args_allowlist`` is non-empty only when the entry uses the
    ``name:arg1,arg2`` syntax. The allowlist is a tuple of prefixes
    that the tool's primary string argument must match.
    """

    name: str
    args_allowlist: tuple[str, ...] = ()

    @property
    def is_wildcard(self) -> bool:
        return any(ch in self.name for ch in "*?[")


def parse_tool_entry(entry: str) -> ToolSpec:
    """Parse a single string from frontmatter ``tools:`` into a :class:`ToolSpec`.

    Accepted shapes:

    * ``read_file``                  → literal name, no args check.
    * ``read_*``                     → wildcard name.
    * ``bash:git status,git diff``   → name + comma-separated arg
      allowlist. Whitespace around each arg is trimmed.
    * ``bash:`` (trailing colon)     → name only, no args allowlist
      (informational shape — same as ``bash``).
    """
    text = (entry or "").strip()
    if ":" not in text:
        return ToolSpec(name=text)
    name, _, raw_allowlist = text.partition(":")
    allowlist = tuple(
        item.strip()
        for item in raw_allowlist.split(",")
        if item.strip()
    )
    return ToolSpec(name=name.strip(), args_allowlist=allowlist)


# ---------------------------------------------------------------------------
# Wildcard matching
# ---------------------------------------------------------------------------


def match_wildcard(pattern: str, name: str) -> bool:
    """Return True iff ``name`` matches ``pattern`` (fnmatch + start-anchor).

    fnmatch normally matches anywhere in the string when there's no
    leading ``*``; we anchor to the start so ``read_*`` does NOT match
    ``read_only_token``-style accidental collisions.
    """
    return fnmatch.fnmatchcase(name, pattern)


# ---------------------------------------------------------------------------
# Args allowlist check
# ---------------------------------------------------------------------------


def args_allowlist_check(
    tool_name: str,
    args: Mapping[str, object],
    allowlist: tuple[str, ...],
) -> bool:
    """Return True if ``args`` satisfies the allowlist.

    Rules:

    * Empty allowlist → True (no restriction).
    * Tool with a single string arg (``bash`` → ``command``,
      ``web_fetch`` → ``url``, etc.) — the value must START WITH
      one of the allowlist entries. This is the most useful shape
      for the M7 use case (`bash:git status,git diff`).
    * Tools without a string arg fall back to True so the wildcard
      surface still works for them.

    The "starts with" semantics are deliberate: an allowlist entry
    like ``git status`` matches both ``git status`` and
    ``git status --short`` — frontmatter can lock down command
    families without spelling out every flag combination.
    """
    if not allowlist:
        return True

    # Pick the first string-typed argument value as the policy target.
    target_value: str | None = None
    for value in args.values():
        if isinstance(value, str):
            target_value = value.strip()
            break

    if target_value is None:
        # No string arg — fall through. The tool's own validation
        # decides whether the call is sane.
        return True

    return any(target_value.startswith(prefix) for prefix in allowlist)


# ---------------------------------------------------------------------------
# Effective tool resolution
# ---------------------------------------------------------------------------


def expand_policy(policy_name: str | None) -> tuple[str, ...]:
    """Return the wildcard set declared by a built-in policy name.

    Unknown names return an empty tuple so the caller can decide
    whether to error or treat as "policy unset".
    """
    if not policy_name:
        return ()
    return BUILTIN_POLICIES.get(policy_name, ())


def resolve_tool_subset(
    parent_tool_names: frozenset[str],
    *,
    explicit_tools: tuple[str, ...] = (),
    policy: str | None = None,
) -> tuple[frozenset[str], dict[str, tuple[str, ...]]]:
    """Compute the effective tool name set + per-tool args allowlists.

    Parameters
    ----------
    parent_tool_names
        Every tool the parent registry exposes. Wildcards in
        ``explicit_tools`` and ``policy`` are matched against this set.
    explicit_tools
        Frontmatter ``tools:`` entries (mix of literals + wildcards
        + ``name:args`` shapes).
    policy
        Frontmatter ``tool_policy:`` value; expanded via
        :func:`expand_policy` and unioned with explicit_tools.

    Returns
    -------
    (allowed_names, per_tool_args)
        ``allowed_names`` is the strict whitelist the registry
        ``filtered()`` call should consume. ``per_tool_args`` maps
        each tool name to its args allowlist (empty tuple when no
        restriction).

    Resolution order:

    1. Start with the policy expansion (if any).
    2. Add every explicit_tools entry.
    3. Match wildcards against parent_tool_names.
    4. Per-tool args allowlists win (a literal entry like
       ``bash:git status`` overrides a bare ``bash`` from the policy).
    """
    # Per-tool args allowlist; later entries win.
    per_tool_args: dict[str, tuple[str, ...]] = {}

    # Collect every wildcard pattern that should be applied.
    patterns: list[str] = list(expand_policy(policy))
    for entry in explicit_tools:
        spec = parse_tool_entry(entry)
        patterns.append(spec.name)
        if not spec.is_wildcard and spec.args_allowlist:
            per_tool_args[spec.name] = spec.args_allowlist

    # Expand patterns against the parent's tool surface.
    allowed: set[str] = set()
    for pattern in patterns:
        if not pattern:
            continue
        for tool_name in parent_tool_names:
            if match_wildcard(pattern, tool_name):
                allowed.add(tool_name)

    return frozenset(allowed), per_tool_args

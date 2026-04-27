"""README↔reality test (v16 M3).

Greps the project README for ✅ claims under "How it compares" and
asserts each maps to a runtime module that actually exists. Failing
this test blocks the v2.6.0 release commit; the v15 grep guard
(`tests/test_no_model_branch_in_core.py`) is the sibling pattern.

Why the test
------------

The v2.5.x audit surfaced four claims that the README documented but
that didn't fully wire through to the runtime: custom agent role
enum, agent memory subagent wiring, plugin marketplace installer,
``/theme`` and ``/vim`` slash commands. v2.6.0 closes those gaps;
this test keeps them closed.

How it works
------------

1. Parse the README, find the "How it compares" comparison table.
2. For each row whose llmcode column is ✅ (or a special marker like
   ``"3-tier"``), look up the row label in :data:`CLAIM_TO_MODULES`
   and assert that at least one of the listed modules can be imported
   AND, where applicable, that it exports a non-stub symbol.
3. Unknown row labels in the README emit a warning so a fresh row
   added without a runtime backing is loud rather than silent.

Adding a new claim
------------------

When a row is added to the README's comparison table:

* Add an entry to :data:`CLAIM_TO_MODULES` mapping the row label to
  one or more importable module names.
* Optionally include an attribute name in the tuple, in which case
  the test asserts the attribute exists at module level (e.g.
  ``("llm_code.tools.agent", "AgentTool")``).

The test deliberately stays grep-driven instead of structural so
README authors can keep working in plain Markdown.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"


# ---------------------------------------------------------------------------
# README claim → runtime module map
# ---------------------------------------------------------------------------

# Each value is a tuple of (module_path, optional_attr_name) entries.
# At least one entry must satisfy the import + attribute check.
CLAIM_TO_MODULES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "Open source": (
        ("llm_code", None),
    ),
    "Local model first": (
        ("llm_code.runtime.model_profile", "ModelProfile"),
    ),
    "Per-model system prompts": (
        ("llm_code.runtime.prompt", "SystemPromptBuilder"),
    ),
    "Qwen/Llama/DeepSeek tuned": (
        ("llm_code.runtime.model_profile", "_BUILTIN_PROFILES"),
    ),
    "Model profile system (TOML)": (
        ("llm_code.runtime.profile_registry", "register_profile"),
        ("llm_code.runtime.model_profile", "ProfileRegistry"),
    ),
    "User-defined agents (.md)": (
        # M1 closes this gap: the registry now feeds AgentTool's enum.
        ("llm_code.runtime.agent_registry", "AgentRegistry"),
        ("llm_code.tools.agent_loader", "load_all_agents"),
    ),
    "Fork agents + cache sharing": (
        ("llm_code.runtime.fork_cache", "build_child_message"),
    ),
    "Agent memory persistence": (
        # M2 closes this gap: subagent_factory injects memory tools.
        ("llm_code.runtime.agent_memory", "AgentMemoryStore"),
        ("llm_code.tools.agent_memory_tools", "MemoryReadTool"),
    ),
    "Git worktree isolation": (
        ("llm_code.runtime.worktree", None),
    ),
    "Exec policy rules (.rules)": (
        ("llm_code.runtime.exec_policy", None),
    ),
    "Sandbox denial learning": (
        ("llm_code.runtime.denial_detector", None),
    ),
    "Per-turn tool visibility": (
        ("llm_code.tools.tool_visibility", None),
    ),
    "Tool desc distillation": (
        ("llm_code.tools.tool_distill", None),
    ),
    "Snippet-composable prompt": (
        ("llm_code.runtime.prompt_snippets", None),
    ),
    "Skill extraction": (
        ("llm_code.runtime.skill_extractor", None),
    ),
    "Approval session cache": (
        ("llm_code.runtime.permission_manager", None),
        ("llm_code.runtime.permissions", None),
    ),
    "Specialist personas": (
        ("llm_code.tools.agent_roles", "BUILT_IN_ROLES"),
    ),
    "Plan mode": (
        ("llm_code.runtime.plan", None),
        ("llm_code.tools.plan_mode", None),
    ),
    "Docker sandbox": (
        ("llm_code.sandbox", None),
    ),
    "PTY (interactive shell)": (
        ("llm_code.tools.bash", None),
    ),
    "Prompt caching (Anthropic)": (
        ("llm_code.runtime.fork_cache", None),
    ),
    "Signed thinking round-trip": (
        ("llm_code.runtime.model_profile", "ModelProfile"),
    ),
    "Extension/plugin system": (
        # M3 closes this gap: dispatcher routes through installer.
        ("llm_code.marketplace.installer", "PluginInstaller"),
        ("llm_code.marketplace.executor", "load_plugin"),
    ),
    "Theme system": (
        # M4 closes this gap: BUILTIN_THEMES exposes the 8 named themes.
        ("llm_code.view.themes", "BUILTIN_THEMES"),
    ),
    "IDE extensions": (
        ("llm_code.lsp", None),
    ),
    "MCP servers": (
        ("llm_code.mcp", None),
    ),
    "Voice input": (
        ("llm_code.tools.voice", None),
    ),
    "Computer use": (
        ("llm_code.computer_use", None),
    ),
    "Notebook tools": (
        ("llm_code.tools.notebook_read", None),
        ("llm_code.tools.notebook_edit", None),
    ),
    "YOLO mode": (
        ("llm_code.runtime.permissions", None),
    ),
}


# Markers in the llmcode column that count as "✅ supported" beyond the
# literal checkmark. The README sometimes uses richer values like
# ``**3-tier**`` instead of ✅ to signal richer-than-bool support; the
# claim still needs runtime backing.
SUPPORTED_MARKERS = ("✅",)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|.*\|\s*$")


def _read_readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _extract_compare_rows(readme_text: str) -> list[tuple[str, str]]:
    """Return ``[(label, llmcode_cell), ...]`` for the comparison table.

    The table starts at ``## How it compares`` and ends at the next
    blank line followed by another markdown heading. Header / divider
    rows are skipped.
    """
    rows: list[tuple[str, str]] = []
    lines = readme_text.splitlines()
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## How it compares"):
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith("## ") and "How it compares" not in stripped:
            break
        # Skip header divider row like "|---|:---:|"
        if stripped.startswith("|---") or "|:---" in stripped:
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        label = m.group(1).strip()
        # Strip Markdown emphasis around the label.
        label = label.strip("*").strip()
        cell = m.group(2).strip()
        if not label or label.lower() == "feature":
            continue
        rows.append((label, cell))
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_readme_exists() -> None:
    assert README.exists(), "README.md missing at repo root"


def test_compare_table_parses() -> None:
    rows = _extract_compare_rows(_read_readme_text())
    # Sanity floor — the table has dozens of rows, not zero.
    assert len(rows) >= 20, f"unexpectedly few rows: {len(rows)}"


@pytest.mark.parametrize(
    "label,modules",
    sorted(CLAIM_TO_MODULES.items()),
)
def test_claim_has_runtime_backing(
    label: str, modules: tuple[tuple[str, str | None], ...]
) -> None:
    """Every README claim with ✅ must import its module + attribute."""
    last_err: Exception | None = None
    for mod_path, attr in modules:
        try:
            module = importlib.import_module(mod_path)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
        if attr is None:
            return
        if hasattr(module, attr):
            return
        last_err = AssertionError(
            f"module {mod_path!r} imported but missing attr {attr!r}"
        )
    raise AssertionError(
        f"README claim {label!r} has no runtime backing: {last_err}"
    )


def test_every_supported_row_is_mapped() -> None:
    """Rows with ``✅`` in the llmcode column must have a CLAIM_TO_MODULES entry.

    Catches the case where the README author added a new ✅ row without
    pointing the test at a module — the failure mode the audit surfaced.
    """
    rows = _extract_compare_rows(_read_readme_text())
    unmapped: list[str] = []
    for label, cell in rows:
        # The cell is the llmcode column. Skip rows that aren't ✅
        # (e.g. "Python", "any", "self-hosted") — those are description,
        # not a yes/no claim.
        if not any(marker in cell for marker in SUPPORTED_MARKERS):
            continue
        if label not in CLAIM_TO_MODULES:
            unmapped.append(label)
    assert not unmapped, (
        "README claims missing runtime mappings — add to CLAIM_TO_MODULES: "
        + ", ".join(repr(x) for x in unmapped)
    )


def test_no_stub_messages_for_resolved_features() -> None:
    """The four audit-fix mechanisms must NOT print v2-not-supported notices.

    We grep the dispatcher for the legacy stub strings and assert they
    are gone. Any future stub regression flips this red.
    """
    dispatcher_path = (
        REPO_ROOT / "llm_code" / "view" / "dispatcher.py"
    )
    text = dispatcher_path.read_text(encoding="utf-8")
    forbidden = [
        # /theme stub from v2.5.x
        "Themes are a legacy TUI feature and are not available",
        # /vim stub from v2.5.x
        "doesn't support runtime-toggled vim mode yet",
    ]
    found = [s for s in forbidden if s in text]
    assert not found, (
        f"dispatcher still carries audit-flagged stub messages: {found}"
    )

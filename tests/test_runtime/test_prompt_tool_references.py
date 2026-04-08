"""Lint: every tool name mentioned inside a TOOL_NAMES marker block in
a system prompt markdown file must exist in the real ToolRegistry.

Catches the failure mode from PRs #11 and #13, where the system prompt
told the model to use a specific tool list that contradicted the actual
registered tools. Without this lint, the contradiction only surfaced
when a user hit the failing query in production."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "llm_code" / "runtime" / "prompts"

_MARKER_BLOCK_RE = re.compile(
    r"<!--\s*TOOL_NAMES:\s*START\s*-->(.*?)<!--\s*TOOL_NAMES:\s*END\s*-->",
    re.DOTALL,
)
_BACKTICKED_IDENT_RE = re.compile(r"`([a-z_][a-z0-9_]*)`")


def _all_registered_tool_names() -> set[str]:
    """Build a ToolRegistry the same way the TUI does and return the
    names. Subagent / optional / plugin tools are out of scope here —
    only the core registered tools count."""
    from llm_code.tools.registry import ToolRegistry
    from llm_code.tools.read_file import ReadFileTool
    from llm_code.tools.write_file import WriteFileTool
    from llm_code.tools.edit_file import EditFileTool
    from llm_code.tools.multi_edit import MultiEditTool
    from llm_code.tools.bash import BashTool
    from llm_code.tools.glob_search import GlobSearchTool
    from llm_code.tools.grep_search import GrepSearchTool
    from llm_code.tools.web_search import WebSearchTool
    from llm_code.tools.web_fetch import WebFetchTool

    registry = ToolRegistry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        MultiEditTool(),
        BashTool(default_timeout=0),
        GlobSearchTool(),
        GrepSearchTool(),
        WebSearchTool(),
        WebFetchTool(),
    ):
        try:
            registry.register(tool)
        except ValueError:
            pass
    return {t.name for t in registry.all_tools()}


def _extract_tool_references(md_path: Path) -> set[str]:
    text = md_path.read_text(encoding="utf-8")
    refs: set[str] = set()
    for match in _MARKER_BLOCK_RE.finditer(text):
        block = match.group(1)
        for ident_match in _BACKTICKED_IDENT_RE.finditer(block):
            refs.add(ident_match.group(1))
    return refs


def _prompt_files_with_markers() -> list[Path]:
    """Return prompt files that contain a TOOL_NAMES marker block."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(
        p for p in PROMPTS_DIR.glob("*.md")
        if _MARKER_BLOCK_RE.search(p.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("prompt_path", _prompt_files_with_markers())
def test_every_prompt_tool_reference_exists_in_registry(prompt_path: Path) -> None:
    refs = _extract_tool_references(prompt_path)
    assert refs, (
        f"{prompt_path.name}: TOOL_NAMES marker block exists but has no "
        f"backticked identifiers in it"
    )
    registered = _all_registered_tool_names()
    dangling = refs - registered
    assert not dangling, (
        f"{prompt_path.name}: system prompt references tools that do NOT "
        f"exist in the ToolRegistry: {sorted(dangling)}. Either add them "
        f"to the registry or remove them from the prompt."
    )


def test_at_least_one_prompt_has_markers() -> None:
    """If no prompt has markers, this lint test silently passes for no
    reason — guard against that."""
    paths = _prompt_files_with_markers()
    assert paths, (
        f"No prompt markdown in {PROMPTS_DIR} has a <!-- TOOL_NAMES: START --> "
        f"/ <!-- TOOL_NAMES: END --> marker block. Add one to qwen.md."
    )

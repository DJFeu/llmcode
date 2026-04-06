"""Guide implementations — feedforward controls that inject context before turns."""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.knowledge_compiler import KnowledgeCompiler
from llm_code.runtime.repo_map import build_repo_map

PLAN_DENIED_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "bash", "git_commit", "git_push", "notebook_edit",
})


def repo_map_guide(cwd: Path, max_tokens: int = 2000) -> str:
    """Build a compact repo map string. Returns empty on failure."""
    try:
        rmap = build_repo_map(cwd)
        return rmap.to_compact(max_tokens=max_tokens)
    except Exception:
        return ""


def analysis_context_guide(context: str | None) -> str:
    """Return stored analysis context, or empty string."""
    return context or ""


def plan_mode_denied_tools(active: bool) -> frozenset[str]:
    """Return the set of tools denied in plan mode. Empty when inactive."""
    if active:
        return PLAN_DENIED_TOOLS
    return frozenset()


def knowledge_guide(cwd: Path, max_tokens: int = 3000) -> str:
    """Return compiled project knowledge for system prompt injection."""
    try:
        compiler = KnowledgeCompiler(cwd=cwd, llm_provider=None)
        return compiler.query(max_tokens=max_tokens)
    except Exception:
        return ""

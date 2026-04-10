"""Memory validation & lint — reject derivable content and check memory health.

This module merges the former ``memory_lint.py`` (stale-ref, coverage-gap,
age, and contradiction checks) with the derivable-content validator so that
all memory quality gates live in one place.

Derivable content (git history, code patterns, file paths, dependency lists)
should be read from the source, not stored in memory.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_code.runtime.memory_taxonomy import MemoryType

from llm_code.api.types import Message, MessageRequest, TextBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Derivable-content detection
# ---------------------------------------------------------------------------

class DerivableContentError(ValueError):
    """Raised in strict mode when memory text contains derivable artifacts."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Derivable content rejected: " + "; ".join(reasons))
        self.reasons = tuple(reasons)


_FENCED_CODE_RE = re.compile(r"```")
_GIT_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
_ABS_PATH_RE = re.compile(r"(?<![\w/])(/[\w./\-]+)")


def _find_derivable(text: str, repo_root: Path) -> list[str]:
    reasons: list[str] = []
    if _FENCED_CODE_RE.search(text):
        reasons.append("contains fenced code blocks (```)")
    if _GIT_SHA_RE.search(text):
        reasons.append("contains 40-char git SHA")
    try:
        root_resolved = repo_root.resolve()
    except OSError:
        root_resolved = repo_root
    for match in _ABS_PATH_RE.findall(text):
        candidate = Path(match)
        try:
            if not candidate.exists():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        reasons.append(f"references on-disk path under repo root: {match}")
        break
    return reasons


def validate_non_derivable(
    text: str,
    repo_root: Path,
    *,
    strict: bool = False,
) -> None:
    """Reject memory text that duplicates derivable artifacts.

    In strict mode, raises :class:`DerivableContentError` when *text* contains
    fenced code blocks, 40-char git SHAs, or absolute paths that exist under
    *repo_root*. In the default warn-only mode (``strict=False``), logs a
    warning and returns silently.
    """
    reasons = _find_derivable(text, repo_root)
    if not reasons:
        return
    if strict:
        raise DerivableContentError(reasons)
    logger.warning(
        "memory.derivable_content_warning: %s",
        "; ".join(reasons),
    )

# Patterns that indicate derivable content
_GIT_LOG_PATTERN = re.compile(r"^[a-f0-9]{7,40}\s+\w", re.MULTILINE)
_FILE_PATH_HEAVY = re.compile(r"(?:^|\n)\s*[-*]\s*/[\w/]+\.\w+", re.MULTILINE)
_DEPENDENCY_LIST = re.compile(
    r"(?:requirements\.txt|package\.json|Cargo\.toml|go\.mod|pyproject\.toml)",
    re.IGNORECASE,
)
_CODE_BLOCK_HEAVY = re.compile(r"```[\s\S]*?```")
_IMPORT_HEAVY = re.compile(r"^(?:import |from .+ import |const .+ = require)", re.MULTILINE)


def validate_content(content: str, memory_type: "MemoryType") -> tuple[bool, str]:
    """Validate whether content is appropriate for memory storage.

    Returns:
        (True, "") if valid, (False, reason) if rejected.
    """
    # Empty content is always invalid
    if not content.strip():
        return False, "Content is empty"

    # Size check (soft — hard limit enforced by TypedMemoryStore)
    if len(content) > 10_000:
        return False, "Content too long (>10,000 chars). Summarize before storing."

    # Check for git log output (looks like commit hashes + messages)
    git_matches = _GIT_LOG_PATTERN.findall(content)
    if len(git_matches) >= 5:
        return False, "Content looks like git log output. Use `git log` to read this — don't store it."

    # Check for heavy file path listings
    path_matches = _FILE_PATH_HEAVY.findall(content)
    content_lines = content.count("\n") + 1
    if path_matches and len(path_matches) > content_lines * 0.5 and len(path_matches) >= 5:
        return False, "Content is mostly file paths. Use `glob_search` to find files — don't store paths."

    # Check for code-heavy content (>60% is code blocks)
    code_blocks = _CODE_BLOCK_HEAVY.findall(content)
    code_chars = sum(len(b) for b in code_blocks)
    if code_chars > len(content) * 0.6 and len(content) > 200:
        return False, "Content is mostly code. Read the source file instead of storing code in memory."

    # Check for import-heavy content (dependency lists)
    import_matches = _IMPORT_HEAVY.findall(content)
    if len(import_matches) >= 8:
        return False, "Content looks like import/dependency listings. These are derivable from source."

    return True, ""


# ---------------------------------------------------------------------------
# Memory lint — stale refs, coverage gaps, age, contradictions  (ex memory_lint.py)
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(r"(?:^|\s)([\w./]+\.(?:py|ts|js|go|rs|md|toml|json|yaml|yml))\b")
_MAX_AGE_DAYS = 30

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".egg-info", ".tox", ".mypy_cache", ".llmcode",
})

_CONTRADICTION_SYSTEM_PROMPT = """\
You are a memory consistency checker. Given a list of project memory entries, \
identify any pairs that contradict each other.

Return a JSON array of objects with keys: "key_a", "key_b", "description".
If no contradictions found, return an empty array: []

Only flag clear factual contradictions, not differences in detail level.
"""


@dataclass(frozen=True)
class StaleReference:
    key: str
    reference: str
    line: int = 0


@dataclass(frozen=True)
class Contradiction:
    key_a: str
    key_b: str
    description: str


@dataclass(frozen=True)
class MemoryLintResult:
    stale: tuple[StaleReference, ...]
    contradictions: tuple[Contradiction, ...]
    coverage_gaps: tuple[str, ...]
    orphans: tuple[str, ...]
    old: tuple[str, ...]

    def format_summary(self) -> str:
        parts = []
        if self.stale:
            parts.append(f"{len(self.stale)} stale")
        if self.coverage_gaps:
            parts.append(f"{len(self.coverage_gaps)} coverage gaps")
        if self.orphans:
            parts.append(f"{len(self.orphans)} orphans")
        if self.old:
            parts.append(f"{len(self.old)} old")
        if self.contradictions:
            parts.append(f"{len(self.contradictions)} contradictions")
        return f"Summary: {', '.join(parts)}" if parts else "Summary: no issues found"

    def format_report(self) -> str:
        lines = ["## Memory Health Check\n"]
        for s in self.stale:
            lines.append(f"  STALE   {s.key}:{s.line}  References \"{s.reference}\" — not found")
        for gap in self.coverage_gaps:
            lines.append(f"  GAP     {gap}  No memory coverage")
        for orphan in self.orphans:
            lines.append(f"  ORPHAN  {orphan}  References nothing in codebase")
        for old_key in self.old:
            lines.append(f"  OLD     {old_key}  Last updated >30 days ago")
        for c in self.contradictions:
            lines.append(f"  CONTRA  {c.key_a} vs {c.key_b}: {c.description}")
        lines.append(f"\n{self.format_summary()}")
        return "\n".join(lines)


def lint_memory(
    memory_dir: Path,
    cwd: Path,
    llm_provider: Any | None = None,
) -> MemoryLintResult:
    """Run fast computational memory health checks."""
    entries = _load_entries(memory_dir)
    stale = _check_stale(entries, cwd)
    coverage_gaps = _check_coverage_gaps(entries, cwd)
    old = _check_old(entries)

    return MemoryLintResult(
        stale=tuple(stale),
        contradictions=(),
        coverage_gaps=tuple(coverage_gaps),
        orphans=(),
        old=tuple(old),
    )


async def lint_memory_deep(
    memory_dir: Path,
    cwd: Path,
    llm_provider: Any | None = None,
) -> MemoryLintResult:
    """Run all checks including LLM contradiction detection."""
    base = lint_memory(memory_dir=memory_dir, cwd=cwd)

    if llm_provider is None:
        return base

    entries = _load_entries(memory_dir)
    contradictions = await _check_contradictions(entries, llm_provider)

    return MemoryLintResult(
        stale=base.stale,
        contradictions=tuple(contradictions),
        coverage_gaps=base.coverage_gaps,
        orphans=base.orphans,
        old=base.old,
    )


def _load_entries(memory_dir: Path) -> dict[str, dict]:
    """Load memory.json entries, excluding internal keys."""
    memory_file = memory_dir / "memory.json"
    if not memory_file.exists():
        return {}
    try:
        data = json.loads(memory_file.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def _check_stale(entries: dict[str, dict], cwd: Path) -> list[StaleReference]:
    """Find memory entries that reference files that no longer exist."""
    stale: list[StaleReference] = []
    for key, entry in entries.items():
        value = entry.get("value", "")
        for line_no, line in enumerate(value.splitlines(), 1):
            for match in _FILE_PATH_RE.finditer(line):
                ref = match.group(1)
                if "/" in ref and not (cwd / ref).exists():
                    stale.append(StaleReference(key=key, reference=ref, line=line_no))
    return stale


def _is_python_package(path: Path) -> bool:
    """Return True if the directory looks like a Python package or source dir."""
    return (path / "__init__.py").exists() or any(path.glob("*.py"))


def _check_coverage_gaps(entries: dict[str, dict], cwd: Path) -> list[str]:
    """Find source directories with no mention in any memory entry."""
    source_dirs: set[str] = set()
    for child in sorted(cwd.iterdir()):
        if not child.is_dir() or child.name in _SKIP_DIRS or child.name.startswith("."):
            continue
        # Qualify top-level dir: has own Python files OR contains Python subdirs
        has_python = _is_python_package(child)
        has_python_subs = any(
            sub.is_dir() and _is_python_package(sub)
            for sub in child.iterdir()
            if sub.name not in _SKIP_DIRS and not sub.name.startswith("_")
        )
        if has_python or has_python_subs:
            for sub in child.iterdir():
                if sub.is_dir() and sub.name not in _SKIP_DIRS and not sub.name.startswith("_"):
                    if _is_python_package(sub):
                        source_dirs.add(f"{child.name}/{sub.name}")

    if not source_dirs:
        return []

    all_values = " ".join(entry.get("value", "") for entry in entries.values())
    gaps: list[str] = []
    for dir_path in sorted(source_dirs):
        dir_name = dir_path.split("/")[-1]
        if dir_name not in all_values and dir_path not in all_values:
            gaps.append(f"{dir_path}/")
    return gaps


def _check_old(entries: dict[str, dict], max_age_days: int = _MAX_AGE_DAYS) -> list[str]:
    """Find entries not updated within max_age_days."""
    old: list[str] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for key, entry in entries.items():
        updated = entry.get("updated_at", "")
        if not updated:
            continue
        try:
            dt = datetime.fromisoformat(updated)
            if dt < cutoff:
                old.append(key)
        except (ValueError, TypeError):
            pass
    return old


async def _check_contradictions(
    entries: dict[str, dict],
    llm_provider: Any,
) -> list[Contradiction]:
    """Use LLM to detect contradictory memory entries."""
    if len(entries) < 2:
        return []

    entries_text = "\n".join(
        f"- [{key}]: {entry.get('value', '')[:200]}"
        for key, entry in entries.items()
    )

    request = MessageRequest(
        model="",
        messages=(Message(role="user", content=(TextBlock(text=f"Memory entries:\n{entries_text}"),)),),
        system=_CONTRADICTION_SYSTEM_PROMPT,
        tools=(),
        max_tokens=512,
        temperature=0.2,
    )

    try:
        response = await llm_provider.send_message(request)
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []

        return [
            Contradiction(
                key_a=item.get("key_a", ""),
                key_b=item.get("key_b", ""),
                description=item.get("description", ""),
            )
            for item in parsed
            if isinstance(item, dict)
        ]
    except Exception:
        logger.debug("Contradiction check failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class MemoryValidator:
    """Unified facade exposing both lint and derivable-content checks."""

    @staticmethod
    def lint(
        memory_dir: Path,
        cwd: Path,
        llm_provider: Any | None = None,
    ) -> MemoryLintResult:
        """Run fast computational memory health checks."""
        return lint_memory(memory_dir=memory_dir, cwd=cwd, llm_provider=llm_provider)

    @staticmethod
    async def lint_deep(
        memory_dir: Path,
        cwd: Path,
        llm_provider: Any | None = None,
    ) -> MemoryLintResult:
        """Run all checks including LLM contradiction detection."""
        return await lint_memory_deep(memory_dir=memory_dir, cwd=cwd, llm_provider=llm_provider)

    @staticmethod
    def check_derivable(
        text: str,
        repo_root: Path,
        *,
        strict: bool = False,
    ) -> None:
        """Check whether *text* contains derivable artifacts.

        Delegates to :func:`validate_non_derivable`.
        """
        return validate_non_derivable(text, repo_root, strict=strict)

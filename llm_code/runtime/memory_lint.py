"""Memory lint — validate project memory for stale refs, gaps, orphans, and age."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from llm_code.api.types import Message, MessageRequest, TextBlock

logger = logging.getLogger(__name__)

_FILE_PATH_RE = re.compile(r"(?:^|\s)([\w./]+\.(?:py|ts|js|go|rs|md|toml|json|yaml|yml))\b")
_MAX_AGE_DAYS = 30

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".egg-info", ".tox", ".mypy_cache", ".llm-code",
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

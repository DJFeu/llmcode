"""Knowledge Compiler — incrementally builds a structured project knowledge base."""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INDEX_LINE_RE = re.compile(r"^- \[(.+?)\]\((.+?)\)\s*—\s*(.+)$")


@dataclass(frozen=True)
class KnowledgeEntry:
    """A single entry in the knowledge index."""

    path: str  # relative to .llm-code/knowledge/
    title: str
    summary: str  # one-line for index
    last_compiled: str  # ISO timestamp
    source_files: tuple[str, ...]  # which source files this knowledge covers


@dataclass(frozen=True)
class IngestResult:
    """Result of the ingest phase."""

    changed_files: tuple[str, ...]
    facts: tuple[str, ...]


class KnowledgeCompiler:
    """Incrementally builds and maintains a structured project knowledge base."""

    def __init__(self, cwd: Path, llm_provider: Any | None, compile_model: str = "") -> None:
        self._cwd = cwd
        self._provider = llm_provider
        self._compile_model = compile_model
        self._knowledge_dir = cwd / ".llm-code" / "knowledge"
        self._knowledge_dir.mkdir(parents=True, exist_ok=True)
        (self._knowledge_dir / "modules").mkdir(exist_ok=True)

    @property
    def knowledge_dir(self) -> Path:
        return self._knowledge_dir

    def get_index(self) -> list[KnowledgeEntry]:
        """Parse index.md and return all knowledge entries."""
        index_path = self._knowledge_dir / "index.md"
        if not index_path.exists():
            return []

        entries: list[KnowledgeEntry] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            m = _INDEX_LINE_RE.match(line.strip())
            if m:
                title, path, summary = m.group(1), m.group(2), m.group(3).strip()
                entries.append(
                    KnowledgeEntry(
                        path=path,
                        title=title,
                        summary=summary,
                        last_compiled="",
                        source_files=(),
                    )
                )
        return entries

    def ingest(
        self,
        facts: list[str] | None = None,
        since_commit: str | None = None,
    ) -> IngestResult:
        """Phase 1: Gather changed files and session facts."""
        changed: list[str] = []
        if since_commit:
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", since_commit, "HEAD"],
                    cwd=self._cwd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    changed = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
            except Exception:
                pass
        return IngestResult(
            changed_files=tuple(changed),
            facts=tuple(facts or []),
        )

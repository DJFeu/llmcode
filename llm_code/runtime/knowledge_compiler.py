"""Knowledge Compiler — incrementally builds a structured project knowledge base."""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_code.api.types import Message, MessageRequest, TextBlock

logger = logging.getLogger(__name__)

_INDEX_LINE_RE = re.compile(r"^- \[(.+?)\]\((.+?)\)\s*—\s*(.+)$")

_COMPILE_SYSTEM_PROMPT = """\
You are a knowledge compiler for a software project. Given a list of changed files \
and session facts, produce a concise Markdown knowledge article about the affected \
module or area.

Format:
# [Module Name]

[2-3 sentence description of what this module does]

## Key Types
- [Important classes, dataclasses, types]

## Patterns
- [Recurring patterns or conventions in this area]

## Dependencies
- [Key imports or integrations]

Be concise and factual. Focus on architecture, not implementation details.
"""


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

    async def compile(self, ingest_data: IngestResult) -> None:
        """Phase 2: Use LLM to compile knowledge from ingested data."""
        if self._provider is None:
            return
        if not ingest_data.changed_files and not ingest_data.facts:
            return

        modules = self._group_by_module(ingest_data.changed_files)

        for module_name, files in modules.items():
            try:
                article = await self._compile_module(module_name, files, ingest_data.facts)
                if article:
                    self._write_module(module_name, article)
            except Exception:
                logger.debug("Knowledge compile failed for module %s", module_name, exc_info=True)

        self._rebuild_index()

    def _group_by_module(self, files: tuple[str, ...]) -> dict[str, list[str]]:
        """Group files by their top-level package directory."""
        modules: dict[str, list[str]] = {}
        for f in files:
            parts = Path(f).parts
            if len(parts) >= 2:
                module = parts[1] if parts[0] in ("llm_code", "src", "lib") else parts[0]
            else:
                module = Path(f).stem
            modules.setdefault(module, []).append(f)
        return modules

    async def _compile_module(
        self, module_name: str, files: list[str], facts: tuple[str, ...]
    ) -> str:
        """Call LLM to generate a knowledge article for a module."""
        existing = ""
        article_path = self._knowledge_dir / "modules" / f"{module_name}.md"
        if article_path.exists():
            existing = article_path.read_text(encoding="utf-8")

        facts_str = "\n".join(f"- {fact}" for fact in facts) if facts else "None"
        files_str = "\n".join(f"- {f}" for f in files)

        user_msg = (
            f"Module: {module_name}\n\n"
            f"Changed files:\n{files_str}\n\n"
            f"Session facts:\n{facts_str}\n\n"
        )
        if existing:
            user_msg += f"Existing article (merge new information, don't overwrite):\n\n{existing}\n"

        request = MessageRequest(
            model=self._compile_model or "",
            messages=(Message(role="user", content=(TextBlock(text=user_msg),)),),
            system=_COMPILE_SYSTEM_PROMPT,
            tools=(),
            max_tokens=1024,
            temperature=0.3,
        )

        response = await self._provider.send_message(request)
        parts: list[str] = []
        for block in response.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts)

    def _write_module(self, module_name: str, content: str) -> None:
        """Write a module article to the knowledge directory."""
        path = self._knowledge_dir / "modules" / f"{module_name}.md"
        path.write_text(content, encoding="utf-8")

    def _rebuild_index(self) -> None:
        """Regenerate index.md from existing module files."""
        modules_dir = self._knowledge_dir / "modules"
        lines = ["# Knowledge Index\n"]
        for md_file in sorted(modules_dir.glob("*.md")):
            title = md_file.stem.replace("_", " ").title()
            summary = ""
            for file_line in md_file.read_text(encoding="utf-8").splitlines():
                stripped = file_line.strip()
                if stripped and not stripped.startswith("#"):
                    summary = stripped
                    break
            lines.append(f"- [{title}]({md_file.relative_to(self._knowledge_dir)}) — {summary}")
        (self._knowledge_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def query(self, max_tokens: int = 3000) -> str:
        """Phase 3: Return relevant knowledge for system prompt injection."""
        entries = self.get_index()
        if not entries:
            return ""

        max_chars = max_tokens * 4
        parts: list[str] = ["# Project Knowledge\n"]
        char_count = len(parts[0])

        for entry in entries:
            article_path = self._knowledge_dir / entry.path
            if not article_path.exists():
                continue
            content = article_path.read_text(encoding="utf-8").strip()
            if char_count + len(content) + 2 > max_chars:
                summary_line = f"- **{entry.title}**: {entry.summary}"
                if char_count + len(summary_line) + 1 <= max_chars:
                    parts.append(summary_line)
                    char_count += len(summary_line) + 1
                break
            parts.append(content)
            char_count += len(content) + 2

        return "\n\n".join(parts) if len(parts) > 1 else ""

"""MemoryContextComponent — render retrieved entries into prompt text.

v12 M7 Task 7.6. Sits between the Reranker and the PromptAssembler:

    Reranker → MemoryContext → PromptAssembler

Consumes a tuple of :class:`MemoryEntry`, renders them through a Jinja2
template under ``engine/prompts/sections/memory/``, and emits the
resulting string on the ``memory_context`` output socket. Empty input
→ empty context so the assembler can drop the whole section cleanly.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from llm_code.engine.component import component, output_types, state_reads
from llm_code.engine.components.memory.schema import MemoryEntry
from llm_code.engine.prompt_builder import PromptBuilder
from llm_code.engine.tracing import traced_component

__all__ = ["MemoryContextComponent"]

_logger = logging.getLogger(__name__)
_DEFAULT_TEMPLATE = "default"


@traced_component
@component
@output_types(memory_context=str, entry_count=int)
@state_reads("memory_entries")
class MemoryContextComponent:
    """Render memory entries as prompt-ready text.

    Args:
        template: Template basename under
            ``engine/prompts/sections/memory/`` — ``default`` or
            ``compact`` today. Extend by dropping a new ``<name>.j2``
            file in that directory.
        max_chars: Soft cap on the total output length. When the
            rendered string exceeds it, the component truncates from
            the **tail** (keeping the most recent + most relevant
            entries intact) and logs a warning.
        templates_dir: Override the default template search root —
            useful for tests that want to exercise custom templates.

    Inputs:
        entries: Tuple of :class:`MemoryEntry`.

    Outputs:
        memory_context: Rendered text; empty string when ``entries``
            is empty.
        entry_count: Number of entries in the rendered section.
    """

    concurrency_group = "cpu_bound"

    def __init__(
        self,
        *,
        template: str = _DEFAULT_TEMPLATE,
        max_chars: int = 4000,
        templates_dir: Path | None = None,
    ) -> None:
        self._template_name = template
        self._max_chars = int(max_chars)
        self._builder = PromptBuilder(
            template_path=f"sections/memory/{template}.j2",
            templates_dir=templates_dir,
        )

    @property
    def template_name(self) -> str:
        return self._template_name

    def run(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> dict[str, Any]:
        if not entries:
            return {"memory_context": "", "entry_count": 0}
        rendered = self._builder.run(
            entries=entries,
            entry_count=len(entries),
        )["prompt"]
        if len(rendered) > self._max_chars:
            _logger.info(
                "MemoryContext truncated from %d to %d chars",
                len(rendered),
                self._max_chars,
            )
            rendered = rendered[: self._max_chars]
        return {
            "memory_context": rendered,
            "entry_count": len(entries),
        }

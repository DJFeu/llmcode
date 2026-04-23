"""PostProcessComponent — format a :class:`ToolResult` for the LLM.

Port of the post-processing inside
:class:`llm_code.runtime.tool_pipeline.ToolExecutionPipeline`:

- Truncate large outputs into a disk-backed summary (``budget_result``).
- Annotate bash permission-denial failures with a hint string (see
  :func:`llm_code.runtime.denial_parser.format_denial_hint`).

The Component emits three output sockets so downstream observers can
branch on the shape without re-parsing the :class:`ToolResult` payload.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 6
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_code.engine.component import component, output_types
from llm_code.tools.base import ToolResult


_DEFAULT_MAX_INLINE = 2000


@component
@output_types(formatted_result=ToolResult, result_text=str, is_error=bool)
class PostProcessComponent:
    """Budget + hint + shape the tool output for the LLM.

    Args:
        max_inline: Max characters before the Component writes the full
            payload to disk and replaces it with a summary. Matches
            the legacy ``_MAX_INLINE_RESULT`` default (2000 chars).
        cache_dir: Directory where overflow payloads are persisted.
            ``None`` disables disk spill — the summary is still
            produced but it omits the "saved to …" hint.
    """

    def __init__(
        self,
        *,
        max_inline: int = _DEFAULT_MAX_INLINE,
        cache_dir: Path | None = None,
    ) -> None:
        self._max_inline = max_inline
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None

    # ------------------------------------------------------------------

    def run(
        self,
        raw_result: ToolResult,
        tool_name: str,
        tool_use_id: str,
    ) -> dict[str, Any]:
        """Apply budgeting + hinting to ``raw_result``."""
        result = raw_result

        # Budget step — mirrors ToolExecutionPipeline.budget_result.
        if len(result.output) > self._max_inline:
            result = self._budget(result, tool_use_id)

        # Bash denial hint — mirrors ToolExecutionPipeline's denial parser.
        if result.is_error and tool_name == "bash" and result.output:
            hinted = self._maybe_append_denial_hint(result)
            result = hinted

        return {
            "formatted_result": result,
            "result_text": result.output,
            "is_error": result.is_error,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _budget(self, result: ToolResult, tool_use_id: str) -> ToolResult:
        """Persist the full output to disk and return a truncated summary.

        When no cache directory is configured we still truncate but
        omit the "saved to" hint; the LLM still sees the prefix and a
        size marker rather than a multi-MB blob.
        """
        head = result.output[: max(self._max_inline // 2, 1000)]
        size = len(result.output)

        if self._cache_dir is None:
            summary = f"{head}\n\n... [{size} chars total, truncated]"
            return ToolResult(
                output=summary,
                is_error=result.is_error,
                metadata=result.metadata,
            )

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_dir / f"{tool_use_id}.txt"
        cache_path.write_text(result.output, encoding="utf-8")
        summary = (
            f"{head}\n\n... [{size} chars total, full output saved to "
            f"{cache_path}. Use read_file to access.]"
        )
        return ToolResult(
            output=summary,
            is_error=result.is_error,
            metadata=result.metadata,
        )

    @staticmethod
    def _maybe_append_denial_hint(result: ToolResult) -> ToolResult:
        """Append a parse-and-format denial hint when relevant.

        The parser is imported lazily so the Component stays importable
        in environments where denial_parser has not been installed yet
        (e.g. lean wheel builds).
        """
        try:
            from llm_code.runtime.denial_parser import (
                format_denial_hint,
                parse_denial,
            )
        except Exception:  # pragma: no cover - defensive
            return result
        info = parse_denial(result.output)
        if info is None:
            return result
        hint = format_denial_hint(info)
        if not hint:
            return result
        return ToolResult(
            output=result.output + hint,
            is_error=result.is_error,
            metadata=result.metadata,
        )

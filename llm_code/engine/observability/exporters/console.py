"""Console span exporter — pretty-prints a span tree to stderr for
local development.

Implements :class:`opentelemetry.sdk.trace.export.SpanExporter` and
buffers spans until ``shutdown()`` (or a manual ``flush_tree()`` call)
so the tree can be rendered in parent-first order. Rich is used for
colour + tree glyphs when it is importable; a plain-text fallback
handles environments without Rich.

Example output::

    agent.run [session=abc123] 4.2s
      |- pipeline.default 3.8s
      |   |- component.PermissionCheck 0.001s
      |   |- component.ToolExecutor[bash] 3.7s
      |- api.stream [model=claude-sonnet-4] 0.3s
"""
from __future__ import annotations

import sys
from typing import Any, Iterable

try:  # pragma: no cover - optional probe
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
        SpanExporter,
        SpanExportResult,
    )

    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment,misc]
    _OTEL_SDK_AVAILABLE = False


try:  # pragma: no cover - optional
    from rich.console import Console as _RichConsole  # type: ignore[import-not-found]
    from rich.tree import Tree as _RichTree  # type: ignore[import-not-found]

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RichConsole = None  # type: ignore[assignment,misc]
    _RichTree = None  # type: ignore[assignment,misc]
    _RICH_AVAILABLE = False


class ConsoleSpanExporter(SpanExporter):  # type: ignore[misc]
    """Render finished spans as a tree on stderr.

    Spans are grouped by trace id; when every root span for a trace is
    complete (i.e. we've seen both the root and all its descendants)
    the tree is printed. :meth:`shutdown` flushes whatever remains —
    useful when the exporter is reused by the local ``llmcode trace``
    CLI or when the process terminates mid-trace.
    """

    def __init__(self, *, stream: Any = None) -> None:
        self._buf: list[Any] = []
        self._stream = stream if stream is not None else sys.stderr

    # ----- OTel SpanExporter protocol ---------------------------------------
    def export(self, spans: Iterable[Any]) -> Any:  # noqa: D401
        for span in spans:
            self._buf.append(span)
        # Flush aggressively — the pretty tree is nice to have but keep
        # the stream up to date so developers see output during a run.
        self.flush_tree()
        if _OTEL_SDK_AVAILABLE:
            return SpanExportResult.SUCCESS
        return None

    def shutdown(self) -> None:
        self.flush_tree()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        self.flush_tree()
        return True

    # ----- pretty rendering -------------------------------------------------
    def flush_tree(self) -> None:
        """Render + clear the buffered spans."""
        if not self._buf:
            return
        try:
            if _RICH_AVAILABLE:
                self._render_rich()
            else:
                self._render_plain()
        finally:
            self._buf.clear()

    def _render_plain(self) -> None:
        """Minimal fallback rendering when Rich is not installed."""
        print("--- spans ---", file=self._stream)
        for span in self._buf:
            duration_s = self._duration_s(span)
            print(
                f"{getattr(span, 'name', '?')} {duration_s:.3f}s",
                file=self._stream,
            )

    def _render_rich(self) -> None:
        """Build a Rich tree from the buffered spans."""
        console = _RichConsole(file=self._stream, force_terminal=False)
        # Group spans by trace id so each trace renders as its own tree.
        by_trace: dict[int, list[Any]] = {}
        for span in self._buf:
            tid = self._trace_id(span)
            by_trace.setdefault(tid, []).append(span)

        for tid, spans in by_trace.items():
            # Sort so parents come before children (OTel timestamps
            # work as a reasonable proxy).
            spans.sort(key=lambda s: getattr(s, "start_time", 0) or 0)
            root = spans[0]
            label = self._format_label(root)
            tree = _RichTree(label)
            # Map span_id -> Rich node so we can hang children off them.
            node_by_id: dict[int, Any] = {self._span_id(root): tree}
            for span in spans[1:]:
                parent_id = self._parent_span_id(span)
                parent_node = node_by_id.get(parent_id, tree)
                node_by_id[self._span_id(span)] = parent_node.add(
                    self._format_label(span)
                )
            console.print(tree)

    # ----- helpers ----------------------------------------------------------
    @staticmethod
    def _duration_s(span: Any) -> float:
        start = getattr(span, "start_time", None)
        end = getattr(span, "end_time", None)
        if start is None or end is None:
            return 0.0
        return (end - start) / 1e9

    @staticmethod
    def _trace_id(span: Any) -> int:
        ctx = getattr(span, "context", None) or getattr(span, "get_span_context", lambda: None)()
        return getattr(ctx, "trace_id", 0) or 0

    @staticmethod
    def _span_id(span: Any) -> int:
        ctx = getattr(span, "context", None) or getattr(span, "get_span_context", lambda: None)()
        return getattr(ctx, "span_id", 0) or 0

    @staticmethod
    def _parent_span_id(span: Any) -> int:
        parent = getattr(span, "parent", None)
        if parent is None:
            return 0
        return getattr(parent, "span_id", 0) or 0

    def _format_label(self, span: Any) -> str:
        name = getattr(span, "name", "?")
        duration = self._duration_s(span)
        return f"{name}  {duration:.3f}s"


def build_console_exporter(config: Any) -> Any:  # noqa: ARG001 - signature parity
    """Factory matching other exporters' ``build_*`` shape."""
    return ConsoleSpanExporter()


__all__ = ["ConsoleSpanExporter", "build_console_exporter"]

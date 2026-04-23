"""Local ``llmcode trace`` subcommand.

Commands:

* ``trace list`` — enumerate the most recent trace files, newest-first.
* ``trace show <id>`` — render the span tree for ``<id>`` as a text tree.
* ``trace tail`` — follow the active trace file and print spans as they
  land; exits when the user hits Ctrl-C.

The CLI is backed by a small JSONL file exporter: each span end writes
one line to ``~/.cache/llmcode/traces/<trace_id>.jsonl``. The file
exporter is intentionally simple (append-only JSONL) so the ``trace
show`` reader is a few lines of :mod:`json` parsing and a BFS over
``parent_span_id``.

This module is **not** wired into ``llm_code.cli.main`` by design — the
user plan reserves that wiring for a follow-up step. Bring the CLI up
in a test harness or call ``trace_cli.cli(...)`` directly from a
wrapper when needed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import click

try:  # pragma: no cover - optional
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
        SpanExporter,
        SpanExportResult,
    )

    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment,misc]
    _OTEL_SDK_AVAILABLE = False


DEFAULT_TRACE_DIR = Path.home() / ".cache" / "llmcode" / "traces"


# ---------------------------------------------------------------------------
# File exporter
# ---------------------------------------------------------------------------
class FileSpanExporter(SpanExporter):  # type: ignore[misc]
    """Write finished spans to ``<dir>/<trace_id>.jsonl``, one line per span.

    The JSON shape is intentionally compact: ``{"trace_id", "span_id",
    "parent_span_id", "name", "start", "end", "attributes"}``. Values
    are stringified enough for JSON — durations are raw nanoseconds,
    ids are hex strings.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self._dir = Path(directory) if directory else DEFAULT_TRACE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def export(self, spans) -> Any:  # noqa: D401
        for span in spans:
            try:
                record = _span_to_record(span)
            except Exception:  # pragma: no cover - defensive
                continue
            path = self._dir / f"{record['trace_id']}.jsonl"
            # ``a`` opens in append mode; os-level atomicity of a single
            # write is enough for readers that tolerate partial lines.
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        if _OTEL_SDK_AVAILABLE:
            return SpanExportResult.SUCCESS
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        return True


def _span_to_record(span: Any) -> dict[str, Any]:
    ctx = getattr(span, "context", None) or span.get_span_context()
    parent = getattr(span, "parent", None)
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent else "",
        "name": getattr(span, "name", "?"),
        "start": getattr(span, "start_time", 0) or 0,
        "end": getattr(span, "end_time", 0) or 0,
        "attributes": _clean_attributes(getattr(span, "attributes", {}) or {}),
    }


def _clean_attributes(attrs: Any) -> dict:
    """Ensure all attribute values are JSON serialisable."""
    out: dict[str, Any] = {}
    for k, v in dict(attrs).items():
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out


# ---------------------------------------------------------------------------
# Tree reconstruction
# ---------------------------------------------------------------------------
@dataclass
class TraceNode:
    """In-memory representation of one span for rendering."""

    span_id: str
    parent_span_id: str
    name: str
    start: int
    end: int
    attributes: dict
    children: list["TraceNode"] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return (self.end - self.start) / 1e9 if self.end > self.start else 0.0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return records


def build_tree(records: list[dict[str, Any]]) -> list[TraceNode]:
    """Rebuild one or more span trees from a JSONL dump.

    Returns the root-level nodes (spans whose ``parent_span_id`` is
    empty or refers to a span not in the list — the latter happens
    when a child span ends after its parent's record has been flushed
    to disk in a separate run).
    """
    nodes: dict[str, TraceNode] = {}
    for r in records:
        node = TraceNode(
            span_id=r["span_id"],
            parent_span_id=r["parent_span_id"],
            name=r["name"],
            start=r["start"],
            end=r["end"],
            attributes=r.get("attributes", {}),
        )
        nodes[node.span_id] = node

    roots: list[TraceNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_span_id)
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)

    # Sort by start time so the rendered tree reflects execution order.
    for node in nodes.values():
        node.children.sort(key=lambda n: n.start)
    roots.sort(key=lambda n: n.start)
    return roots


def render_tree(
    roots: list[TraceNode], *, indent: str = "  "
) -> Iterator[str]:
    """Yield one line of text per span in pre-order, with indentation."""

    def _walk(node: TraceNode, depth: int) -> Iterator[str]:
        yield f"{indent * depth}- {node.name}  {node.duration_s:.3f}s"
        for child in node.children:
            yield from _walk(child, depth + 1)

    for root in roots:
        yield from _walk(root, 0)


# ---------------------------------------------------------------------------
# click CLI
# ---------------------------------------------------------------------------
@click.group()
def cli() -> None:
    """Inspect local trace files written by the JSONL file exporter."""


@cli.command(name="list")
@click.option(
    "--dir", "trace_dir", default=None,
    help=f"Trace directory (default {DEFAULT_TRACE_DIR}).",
)
@click.option("--limit", default=20, help="Max entries to show.")
def list_cmd(trace_dir: Optional[str], limit: int) -> None:
    """List recent trace files, newest-first."""
    base = Path(trace_dir) if trace_dir else DEFAULT_TRACE_DIR
    if not base.exists():
        click.echo("no trace directory yet")
        return

    entries = sorted(
        base.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in entries[:limit]:
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        click.echo(f"{path.stem}  {mtime}  {path.stat().st_size} bytes")


@cli.command(name="show")
@click.argument("trace_id")
@click.option("--dir", "trace_dir", default=None)
def show_cmd(trace_id: str, trace_dir: Optional[str]) -> None:
    """Render the span tree for ``trace_id``."""
    base = Path(trace_dir) if trace_dir else DEFAULT_TRACE_DIR
    path = base / f"{trace_id}.jsonl"
    if not path.exists():
        click.echo(f"trace {trace_id} not found at {path}", err=True)
        raise click.exceptions.Exit(code=1)
    records = _load_jsonl(path)
    roots = build_tree(records)
    for line in render_tree(roots):
        click.echo(line)


@cli.command(name="tail")
@click.option("--dir", "trace_dir", default=None)
@click.option(
    "--interval", default=0.5, help="Poll interval in seconds.",
)
def tail_cmd(trace_dir: Optional[str], interval: float) -> None:
    """Follow the newest trace file, printing spans as they land."""
    base = Path(trace_dir) if trace_dir else DEFAULT_TRACE_DIR
    base.mkdir(parents=True, exist_ok=True)

    seen_size = 0
    current_path: Optional[Path] = None
    try:
        while True:
            candidates = sorted(
                base.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                time.sleep(interval)
                continue
            newest = candidates[0]
            if newest != current_path:
                current_path = newest
                seen_size = 0
                click.echo(f"==> following {newest}")
            size = newest.stat().st_size
            if size > seen_size:
                with newest.open("r", encoding="utf-8") as f:
                    f.seek(seen_size)
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        click.echo(f"{rec.get('name', '?')}  {rec.get('span_id', '')}")
                seen_size = size
            time.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        click.echo("")


__all__ = [
    "DEFAULT_TRACE_DIR",
    "FileSpanExporter",
    "TraceNode",
    "build_tree",
    "cli",
    "render_tree",
]

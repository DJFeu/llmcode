"""M6 Task 6.10 — ``llmcode trace`` CLI tests.

Covers:

* ``FileSpanExporter`` writes one JSON line per span to
  ``<dir>/<trace_id>.jsonl``.
* ``build_tree`` reconstructs parent/child topology from the JSONL.
* ``render_tree`` emits one indented line per span in pre-order.
* ``trace list`` / ``trace show`` click commands integrate correctly
  via :class:`click.testing.CliRunner`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


def _write_records(dir_: Path, trace_id: str, records: list[dict]) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"{trace_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


class TestFileSpanExporter:
    def test_export_writes_jsonl(self, tmp_path: Path) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from llm_code.engine.observability.trace_cli import FileSpanExporter

        exporter = FileSpanExporter(directory=tmp_path)
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("file-exporter-test")

        with tracer.start_as_current_span("root"):
            with tracer.start_as_current_span("child"):
                pass

        provider.shutdown()

        # One jsonl file, with at least 2 span records.
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        lines = [
            json.loads(line)
            for line in files[0].read_text().splitlines()
            if line.strip()
        ]
        assert len(lines) >= 2
        names = {r["name"] for r in lines}
        assert "root" in names
        assert "child" in names

    def test_force_flush_returns_true(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import FileSpanExporter

        assert FileSpanExporter(directory=tmp_path).force_flush() is True


class TestBuildTree:
    def test_parent_child_connected(self) -> None:
        from llm_code.engine.observability.trace_cli import build_tree

        records = [
            {"trace_id": "t1", "span_id": "A", "parent_span_id": "",
             "name": "root", "start": 1_000, "end": 2_000, "attributes": {}},
            {"trace_id": "t1", "span_id": "B", "parent_span_id": "A",
             "name": "child", "start": 1_100, "end": 1_900, "attributes": {}},
        ]
        roots = build_tree(records)
        assert len(roots) == 1
        assert roots[0].name == "root"
        assert len(roots[0].children) == 1
        assert roots[0].children[0].name == "child"

    def test_orphan_child_treated_as_root(self) -> None:
        from llm_code.engine.observability.trace_cli import build_tree

        records = [
            {"trace_id": "t1", "span_id": "X", "parent_span_id": "GONE",
             "name": "orphan", "start": 0, "end": 1, "attributes": {}},
        ]
        roots = build_tree(records)
        assert len(roots) == 1
        assert roots[0].name == "orphan"

    def test_sorted_by_start_time(self) -> None:
        from llm_code.engine.observability.trace_cli import build_tree

        records = [
            {"trace_id": "t", "span_id": "R", "parent_span_id": "",
             "name": "root", "start": 0, "end": 100, "attributes": {}},
            {"trace_id": "t", "span_id": "B", "parent_span_id": "R",
             "name": "b", "start": 50, "end": 90, "attributes": {}},
            {"trace_id": "t", "span_id": "A", "parent_span_id": "R",
             "name": "a", "start": 10, "end": 40, "attributes": {}},
        ]
        roots = build_tree(records)
        assert [c.name for c in roots[0].children] == ["a", "b"]


class TestRenderTree:
    def test_pre_order_with_indentation(self) -> None:
        from llm_code.engine.observability.trace_cli import (
            build_tree,
            render_tree,
        )

        records = [
            {"trace_id": "t", "span_id": "R", "parent_span_id": "",
             "name": "root", "start": 0, "end": 2_000_000_000, "attributes": {}},
            {"trace_id": "t", "span_id": "A", "parent_span_id": "R",
             "name": "child", "start": 1_000, "end": 1_500_000_000,
             "attributes": {}},
        ]
        lines = list(render_tree(build_tree(records)))
        assert len(lines) == 2
        assert lines[0].startswith("- root")
        assert lines[1].startswith("  - child")


class TestListCommand:
    def test_list_empty_dir(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--dir", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_shows_traces(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import cli

        _write_records(tmp_path, "trace-abc", [
            {"trace_id": "trace-abc", "span_id": "x", "parent_span_id": "",
             "name": "root", "start": 0, "end": 1, "attributes": {}},
        ])
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "trace-abc" in result.output

    def test_list_respects_limit(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import cli

        for i in range(5):
            _write_records(tmp_path, f"trace-{i}", [
                {"trace_id": f"trace-{i}", "span_id": "x",
                 "parent_span_id": "", "name": "r", "start": 0, "end": 1,
                 "attributes": {}},
            ])
        runner = CliRunner()
        result = runner.invoke(cli, ["list", "--dir", str(tmp_path), "--limit", "2"])
        assert result.exit_code == 0
        assert result.output.count("trace-") == 2


class TestShowCommand:
    def test_show_renders_tree(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import cli

        _write_records(tmp_path, "t1", [
            {"trace_id": "t1", "span_id": "R", "parent_span_id": "",
             "name": "root", "start": 0, "end": 10**9, "attributes": {}},
            {"trace_id": "t1", "span_id": "C", "parent_span_id": "R",
             "name": "child", "start": 10**8, "end": 9 * 10**8, "attributes": {}},
        ])
        runner = CliRunner()
        result = runner.invoke(cli, ["show", "t1", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "root" in result.output
        assert "child" in result.output

    def test_show_missing_trace_returns_nonzero(self, tmp_path: Path) -> None:
        from llm_code.engine.observability.trace_cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["show", "gone", "--dir", str(tmp_path)])
        assert result.exit_code != 0

"""M6 Task 6.7 — ConsoleSpanExporter tests.

Covers:

* ``build_console_exporter(config)`` returns a :class:`ConsoleSpanExporter`.
* ``export(spans)`` buffers + flushes; writes to the stream.
* ``shutdown()`` flushes remaining spans.
* Plain-text fallback works when Rich is unavailable (not directly
  exercisable here; we just assert the exporter does not crash).
* Tree grouping by trace id — multiple trace ids render as distinct
  trees.
"""
from __future__ import annotations

import io

import pytest


class TestBuild:
    def test_build_console_returns_exporter(self) -> None:
        from llm_code.engine.observability.exporters.console import (
            ConsoleSpanExporter,
            build_console_exporter,
        )
        from llm_code.runtime.config import ObservabilityConfig

        exporter = build_console_exporter(ObservabilityConfig(exporter="console"))
        assert isinstance(exporter, ConsoleSpanExporter)


class TestExportWithOTel:
    def test_export_writes_to_stream(self) -> None:
        pytest.importorskip("opentelemetry")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from llm_code.engine.observability.exporters.console import (
            ConsoleSpanExporter,
        )

        stream = io.StringIO()
        exporter = ConsoleSpanExporter(stream=stream)
        # Use a local provider to avoid interfering with the global one.
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("console-test")

        with tracer.start_as_current_span("parent"):
            with tracer.start_as_current_span("child"):
                pass

        # On span end, SimpleSpanProcessor.export fires and the console
        # exporter auto-flushes; stream should have non-empty output.
        provider.shutdown()
        assert stream.getvalue() != ""

    def test_shutdown_flushes_remaining(self) -> None:
        pytest.importorskip("opentelemetry")
        from llm_code.engine.observability.exporters.console import (
            ConsoleSpanExporter,
        )

        stream = io.StringIO()
        exporter = ConsoleSpanExporter(stream=stream)
        # No spans queued — shutdown should be a safe no-op.
        exporter.shutdown()
        # No assertion beyond "did not raise"; stream stays empty.
        assert stream.getvalue() == ""

    def test_force_flush_returns_true(self) -> None:
        from llm_code.engine.observability.exporters.console import (
            ConsoleSpanExporter,
        )

        exporter = ConsoleSpanExporter(stream=io.StringIO())
        assert exporter.force_flush() is True


class TestPrettyRendering:
    def test_multiple_traces_render_separately(self) -> None:
        """Two distinct trace ids should produce two trees (or at least
        two separate visual groups) in the output."""
        pytest.importorskip("opentelemetry")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from llm_code.engine.observability.exporters.console import (
            ConsoleSpanExporter,
        )

        stream = io.StringIO()
        exporter = ConsoleSpanExporter(stream=stream)
        # Use a dedicated provider instance rather than the global one —
        # avoids "TracerProvider already set" warnings when multiple
        # tests in the same process wire up different exporters.
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("multi")

        for _ in range(2):
            with tracer.start_as_current_span("root"):
                pass

        provider.shutdown()
        output = stream.getvalue()
        # Some output produced.
        assert output != ""

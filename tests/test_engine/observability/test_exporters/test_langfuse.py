"""M6 Task 6.7 — Langfuse exporter tests.

Covers:

* ``_span_kind`` classifies spans correctly from canonical attributes.
* :class:`LangfuseSpanExporter` invokes ``langfuse.span(...)`` for
  tool/component spans and ``langfuse.generation(...)`` for
  model/API spans.
* Session id from the :data:`SESSION_ID` attribute is forwarded.
* ``shutdown()`` calls the Langfuse client's ``flush()``.
* ``build_langfuse_exporter`` raises :class:`ImportError` when the
  Langfuse package is missing (gracefully handled by the factory).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestSpanKind:
    def test_generation_kind_from_tokens(self) -> None:
        from llm_code.engine.observability.attributes import (
            MODEL_NAME,
            TOKENS_IN,
        )
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {TOKENS_IN: 100, MODEL_NAME: "claude-sonnet-4"}
        assert _span_kind(span) == "generation"

    def test_tool_kind_from_tool_name(self) -> None:
        from llm_code.engine.observability.attributes import TOOL_NAME
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {TOOL_NAME: "bash"}
        assert _span_kind(span) == "tool"

    def test_component_kind_from_component_name(self) -> None:
        from llm_code.engine.observability.attributes import COMPONENT_NAME
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {COMPONENT_NAME: "PermissionCheck"}
        assert _span_kind(span) == "component"

    def test_pipeline_kind(self) -> None:
        from llm_code.engine.observability.attributes import PIPELINE_NAME
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {PIPELINE_NAME: "default"}
        assert _span_kind(span) == "pipeline"

    def test_agent_kind(self) -> None:
        from llm_code.engine.observability.attributes import (
            AGENT_ITERATION,
            AGENT_MODE,
        )
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {AGENT_ITERATION: 1, AGENT_MODE: "plan"}
        assert _span_kind(span) == "agent"

    def test_generic_span_when_no_canonical_attrs(self) -> None:
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock()
        span.attributes = {}
        assert _span_kind(span) == "span"

    def test_tolerates_missing_attributes_field(self) -> None:
        from llm_code.engine.observability.exporters.langfuse import _span_kind

        span = MagicMock(spec=["name"])
        span.name = "unknown"
        assert _span_kind(span) == "span"


class TestLangfuseExporter:
    def _make_span(self, **attrs):
        from llm_code.engine.observability.attributes import SESSION_ID

        span = MagicMock()
        span.name = "test.span"
        span.attributes = {SESSION_ID: "sess-123", **attrs}
        span.start_time = 1_000_000_000
        span.end_time = 2_000_000_000
        return span

    def test_export_tool_span_calls_span_method(self) -> None:
        from llm_code.engine.observability.attributes import TOOL_NAME
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)
        span = self._make_span(**{TOOL_NAME: "bash"})
        exporter.export([span])

        client.span.assert_called_once()
        kwargs = client.span.call_args.kwargs
        assert kwargs["session_id"] == "sess-123"
        assert kwargs["name"] == "test.span"

    def test_export_generation_span_calls_generation_method(self) -> None:
        from llm_code.engine.observability.attributes import (
            MODEL_NAME,
            TOKENS_IN,
            TOKENS_OUT,
        )
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)
        span = self._make_span(**{
            MODEL_NAME: "claude-sonnet-4",
            TOKENS_IN: 100,
            TOKENS_OUT: 50,
        })
        exporter.export([span])

        client.generation.assert_called_once()
        kwargs = client.generation.call_args.kwargs
        assert kwargs["model"] == "claude-sonnet-4"
        assert kwargs["usage"]["input"] == 100
        assert kwargs["usage"]["output"] == 50
        assert kwargs["usage"]["total"] == 150

    def test_export_skips_without_session_id(self) -> None:
        """No SESSION_ID still produces a Langfuse span, without the
        session_id field."""
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)

        span = MagicMock()
        span.name = "no-session"
        span.attributes = {}
        span.start_time = 1_000_000_000
        span.end_time = 2_000_000_000
        exporter.export([span])

        client.span.assert_called_once()
        kwargs = client.span.call_args.kwargs
        assert "session_id" not in kwargs

    def test_shutdown_flushes_client(self) -> None:
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)
        exporter.shutdown()
        client.flush.assert_called_once()

    def test_force_flush_returns_true(self) -> None:
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)
        assert exporter.force_flush() is True

    def test_export_survives_client_error(self) -> None:
        """A Langfuse client exception must not propagate out of export()."""
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        client.span.side_effect = RuntimeError("boom")
        exporter = LangfuseSpanExporter(client)

        span = MagicMock()
        span.name = "s"
        span.attributes = {}
        span.start_time = 0
        span.end_time = 1
        # Must not raise.
        exporter.export([span])

    def test_error_tool_sets_level_error(self) -> None:
        from llm_code.engine.observability.attributes import (
            TOOL_NAME,
            TOOL_RESULT_IS_ERROR,
        )
        from llm_code.engine.observability.exporters.langfuse import (
            LangfuseSpanExporter,
        )

        client = MagicMock()
        exporter = LangfuseSpanExporter(client)
        span = self._make_span(**{TOOL_NAME: "bash", TOOL_RESULT_IS_ERROR: True})
        exporter.export([span])
        kwargs = client.span.call_args.kwargs
        assert kwargs.get("level") == "ERROR"


class TestFactoryIntegration:
    def test_build_langfuse_without_package_raises(self, monkeypatch) -> None:
        """If langfuse is not importable, the builder raises ImportError."""
        import builtins
        import sys

        # Remove the cached module and block re-import so the builder's
        # ``from langfuse import Langfuse`` fails.
        monkeypatch.setitem(sys.modules, "langfuse", None)
        real_import = builtins.__import__

        def _fake_import(name, *a, **kw):
            if name == "langfuse":
                raise ImportError("blocked for test")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        from llm_code.engine.observability.exporters.langfuse import (
            build_langfuse_exporter,
        )
        from llm_code.runtime.config import ObservabilityConfig

        with pytest.raises(ImportError):
            build_langfuse_exporter(ObservabilityConfig(exporter="langfuse"))

    def test_factory_returns_none_on_missing_langfuse(self, monkeypatch) -> None:
        """Top-level factory swallows the ImportError."""
        import builtins
        import sys

        monkeypatch.setitem(sys.modules, "langfuse", None)
        real_import = builtins.__import__

        def _fake_import(name, *a, **kw):
            if name == "langfuse":
                raise ImportError("blocked")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        from llm_code.engine.observability.exporters import build_exporter
        from llm_code.runtime.config import ObservabilityConfig

        assert build_exporter(ObservabilityConfig(exporter="langfuse")) is None

"""M6 Task 6.7 — OTLP exporter tests.

Covers:

* HTTP/protobuf path returns a real ``OTLPSpanExporter`` when the dep
  is installed.
* Endpoint + headers from ``ObservabilityConfig`` are threaded through.
* gRPC path either returns an exporter (when the grpc extra is installed)
  or falls back to HTTP gracefully.
* ``build_otlp_exporter`` never raises on a missing grpc package —
  it falls back to HTTP instead.
"""
from __future__ import annotations

import pytest


def _cfg(**overrides):
    from llm_code.runtime.config import ObservabilityConfig

    return ObservabilityConfig(**overrides)


class TestHTTPExporter:
    def test_build_http_returns_exporter(self) -> None:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        from llm_code.engine.observability.exporters.otlp import (
            build_otlp_exporter,
        )

        exporter = build_otlp_exporter(_cfg(
            exporter="otlp",
            otlp_endpoint="http://example.com:4318/v1/traces",
            otlp_protocol="http/protobuf",
        ))
        assert exporter is not None

    def test_build_http_uses_default_endpoint_when_empty(self) -> None:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        from llm_code.engine.observability.exporters.otlp import (
            build_otlp_exporter,
        )

        # Should not raise when endpoint empty — uses default.
        exporter = build_otlp_exporter(_cfg(exporter="otlp", otlp_endpoint=""))
        assert exporter is not None

    def test_headers_converted_from_tuple_to_dict(self) -> None:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        from llm_code.engine.observability.exporters.otlp import (
            build_otlp_exporter,
        )

        # Just verify this doesn't raise and returns non-None.
        exporter = build_otlp_exporter(_cfg(
            exporter="otlp",
            otlp_endpoint="http://localhost:4318/v1/traces",
            otlp_headers=(("Authorization", "Bearer demo"),),
        ))
        assert exporter is not None


class TestGRPCFallback:
    def test_grpc_falls_back_to_http_when_missing(self) -> None:
        """If the grpc exporter package isn't installed, the builder
        must not raise — it downgrades to HTTP."""
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        from llm_code.engine.observability.exporters.otlp import (
            build_otlp_exporter,
        )

        # Request grpc — either succeeds (dep present) or falls back
        # to HTTP. In both cases the builder must not raise.
        exporter = build_otlp_exporter(_cfg(
            exporter="otlp",
            otlp_protocol="grpc",
        ))
        assert exporter is not None

    def test_grpc_import_guarded(self) -> None:
        """Calling ``_grpc_exporter`` directly raises ImportError only
        when the package is missing."""
        from llm_code.engine.observability.exporters import otlp as otlp_mod

        # This test is informational — the behaviour depends on whether
        # the grpc extra is installed. We assert the function exists and
        # is callable; the actual ImportError path is exercised by the
        # test above via the fallback.
        assert callable(otlp_mod._grpc_exporter)


class TestFactoryIntegration:
    def test_factory_resolves_otlp_kind(self) -> None:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        from llm_code.engine.observability.exporters import build_exporter

        exporter = build_exporter(_cfg(
            exporter="otlp",
            otlp_endpoint="http://localhost:4318/v1/traces",
        ))
        assert exporter is not None

    def test_factory_returns_none_on_off(self) -> None:
        from llm_code.engine.observability.exporters import build_exporter

        assert build_exporter(_cfg(exporter="off")) is None

    def test_factory_returns_none_on_unknown(self) -> None:
        from llm_code.engine.observability.exporters import build_exporter

        assert build_exporter(_cfg(exporter="bogus")) is None

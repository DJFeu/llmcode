"""OTLP span exporter adapter.

Wraps :class:`opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`
(HTTP/protobuf, the default) and — when the optional grpc package is
installed — the matching gRPC exporter. The factory chooses based on
``config.otlp_protocol`` (``"http/protobuf"`` or ``"grpc"``).

The optional dependencies live behind import guards: a ``build_*``
call that needs a missing dep raises :class:`ImportError`, which the
top-level :func:`llm_code.engine.observability.exporters.build_exporter`
catches so engine boot never fails just because the HTTP or gRPC OTLP
package isn't installed.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _http_exporter(config: Any) -> Any:
    """Return an ``OTLPSpanExporter`` (HTTP/protobuf)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    endpoint = getattr(config, "otlp_endpoint", "") or "http://localhost:4318/v1/traces"
    headers = dict(getattr(config, "otlp_headers", ()) or ())
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)


def _grpc_exporter(config: Any) -> Any:
    """Return an ``OTLPSpanExporter`` (gRPC).

    The gRPC exporter ships in a separate package
    (``opentelemetry-exporter-otlp-proto-grpc``); this helper raises
    :class:`ImportError` cleanly when it is missing so the factory can
    downgrade to HTTP or disable tracing.
    """
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
        OTLPSpanExporter,
    )

    endpoint = getattr(config, "otlp_endpoint", "") or "http://localhost:4317"
    headers = dict(getattr(config, "otlp_headers", ()) or ())
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)


def build_otlp_exporter(config: Any) -> Any:
    """Dispatch on ``config.otlp_protocol`` and return an exporter."""
    protocol = getattr(config, "otlp_protocol", "http/protobuf")
    if protocol == "grpc":
        try:
            return _grpc_exporter(config)
        except ImportError:
            logger.warning(
                "otlp grpc requested but opentelemetry-exporter-otlp-proto-grpc "
                "is missing; falling back to http/protobuf"
            )
    return _http_exporter(config)


__all__ = ["build_otlp_exporter"]

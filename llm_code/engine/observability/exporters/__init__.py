"""Exporter factory + registry.

Usage::

    from llm_code.engine.observability.exporters import build_exporter
    exporter = build_exporter(config)

The factory dispatches on ``config.exporter`` and returns the matching
:class:`~opentelemetry.sdk.trace.export.SpanExporter`. When the target
exporter requires an optional package (e.g. ``opentelemetry-exporter-
otlp-proto-http`` or ``langfuse``) that is not installed, the factory
catches the :class:`ImportError` and returns ``None`` — the caller
should then fall back to a console exporter or disable tracing so
engine boot still succeeds.

``build_exporter(cfg) -> SpanExporter | None``
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def build_exporter(config: Any) -> Optional[Any]:
    """Return a :class:`SpanExporter` for ``config.exporter``.

    Returns ``None`` when:

    * ``config.exporter == "off"`` — tracing disabled.
    * The selected exporter's optional dep is missing.
    * OTel itself is not installed.
    """
    kind = getattr(config, "exporter", "off")
    if kind == "off":
        return None

    try:
        if kind == "console":
            from llm_code.engine.observability.exporters.console import (
                build_console_exporter,
            )
            return build_console_exporter(config)
        if kind == "otlp":
            from llm_code.engine.observability.exporters.otlp import (
                build_otlp_exporter,
            )
            return build_otlp_exporter(config)
        if kind == "langfuse":
            from llm_code.engine.observability.exporters.langfuse import (
                build_langfuse_exporter,
            )
            return build_langfuse_exporter(config)
    except ImportError as exc:
        logger.warning(
            "exporter %r requested but optional dep is missing: %s", kind, exc,
        )
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("exporter %r failed to init: %s", kind, exc)
        return None

    logger.warning("unknown exporter kind %r — tracing disabled", kind)
    return None


__all__ = ["build_exporter"]

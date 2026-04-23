"""v12 observability — M6. OpenTelemetry + Langfuse + redaction + metrics.

Subsystems:
- redaction.py: PII + secret scrubber (logging + span attributes)
- metrics.py: Prometheus canonical metrics
- attributes.py: canonical span attribute name constants + allow-list
- exporters/: OTLP / Langfuse / Console adapters (M6)

Public API lives in `llm_code.engine.tracing`.
"""
from __future__ import annotations

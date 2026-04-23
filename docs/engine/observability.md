# Observability (M6)

> **Status:** v12 M6 — OpenTelemetry, Langfuse, Prometheus, and PII
> redaction wired into the engine by default.

## At a glance

- **Tracing:** every `Pipeline.run`, `Component.run` / `run_async`, plus
  explicit `agent_span` / `tool_call_span` / `api_span` / `pipeline_span`
  context managers emit an OpenTelemetry span.
- **Exporters:** OTLP (HTTP/protobuf, optional gRPC), Langfuse,
  pretty-console, and a local JSONL file exporter that backs
  `llmcode trace`.
- **Metrics:** six canonical Prometheus metrics, exposed at `/metrics`
  when Hayhooks is running.
- **Redaction:** a regex-based PII/secret scrubber filters every
  logging `LogRecord` and every span attribute before export.
- **Propagation:** `contextvars`-backed helpers survive the
  `asyncio ↔ thread` boundary, so sub-agent spawns appear as children
  of the parent span.

All observability dependencies are optional — when the relevant package
is not installed the subsystem degrades to a no-op so the engine still
boots cleanly.

## Enabling it

```toml
# ~/.llmcode/config.json (or project-local)
[engine.observability]
enabled = true
exporter = "console"        # one of: "console" | "otlp" | "langfuse" | "off"
sample_rate = 1.0
redact_log_records = true
redact_span_attributes = true
metrics_enabled = true
```

At engine boot `llm_code.engine.tracing.trace_init(config)` is called
with the loaded `ObservabilityConfig`; it wires the
`TracerProvider`, installs the redacting log filter, and starts any
metrics endpoint required by the chosen exporter.

## Choosing an exporter

| Exporter   | When to use                                              |
|------------|-----------------------------------------------------------|
| `console`  | Local dev: pretty-prints the span tree to stderr.          |
| `otlp`     | Production: send spans to any OTLP collector (Jaeger, Grafana Tempo, Honeycomb). |
| `langfuse` | LLM-observability SaaS; maps model spans to generations with token accounting. |
| `off`      | Kill switch — no spans emitted, no exporter started.       |

See [observability_exporters.md](observability_exporters.md) for setup
instructions per exporter.

## Reading a trace tree

A typical agent run produces:

```
pipeline.<PipelineClass>
  - component.PermissionCheck
  - component.RateLimiter
  - component.ToolExecutor (llmcode.tool.name=bash)
api.stream (gen_ai.request.model=claude-sonnet-4)
```

Sub-agent spawns appear as children of the parent agent span because
the spawn path calls
`propagate_across_to_thread()` + `apply_context()` so the
parent OTel context is re-attached on the worker thread.

## Metrics

Six canonical Prometheus metrics are defined in
`llm_code.engine.observability.metrics`:

| Metric                             | Labels              |
|------------------------------------|---------------------|
| `engine_pipeline_runs`             | `outcome`           |
| `engine_pipeline_duration_seconds` | —                   |
| `engine_component_duration_seconds`| `component`         |
| `engine_agent_iterations`          | `mode`, `exit_reason` |
| `engine_tool_invocations`          | `tool`, `status`    |
| `engine_api_tokens`                | `direction`, `model` |

Scrape `http://{hayhooks.host}:{hayhooks.port}/metrics`; the endpoint
returns the standard Prometheus text format.

## Redaction

Span attributes, log records, and exported payloads pass through a
corpus-tested scrubber before hitting the wire. Coverage:

- OpenAI / Anthropic / GCP / AWS / Slack / GitHub credentials
- JWTs and Bearer headers
- Base64 blobs > 120 chars
- Emails (configurable)

See [observability_redaction.md](observability_redaction.md) for the
full pattern list and guidance on extending it for user-specific
secret formats.

## `llmcode trace` CLI

The local JSONL file exporter writes one span per line to
`~/.cache/llmcode/traces/<trace_id>.jsonl`. The
`llm_code.engine.observability.trace_cli` click group ships three
commands:

```bash
python -m llm_code.engine.observability.trace_cli list
python -m llm_code.engine.observability.trace_cli show <trace_id>
python -m llm_code.engine.observability.trace_cli tail
```

Wiring this into the top-level `llmcode` CLI is tracked as a follow-up
task; until then, invoke the module directly or import
`llm_code.engine.observability.trace_cli.cli` from a wrapper script.

## Public API

```python
from llm_code.engine.tracing import (
    trace_init,                 # call once at engine boot
    traced_component,           # class decorator (applied by @component)
    traced_pipeline,            # class decorator (pipeline-level spans)
    agent_span,                 # ctx manager: per agent iteration
    tool_call_span,             # ctx manager: per tool invocation
    pipeline_span,              # ctx manager: pipeline scope
    api_span,                   # ctx manager: per model/API call
)

from llm_code.engine.observability.propagation import (
    propagate_across_to_thread, # capture ctx for asyncio.to_thread
    apply_context,              # re-attach ctx on worker side
    inject_parent_into_span,    # explicit parent link for subagent
)

from llm_code.engine.observability.redaction import (
    DEFAULT_PATTERNS, Redactor, RedactingFilter,
)
```

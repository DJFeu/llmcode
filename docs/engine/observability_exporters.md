# Observability Exporters

> **Status:** v12 M6 — three OpenTelemetry span exporters ship with
> the engine: `console`, `otlp`, `langfuse`. Selection is a one-line
> config change; dependencies are optional.

## Section table of contents

1. Picking an exporter
2. Console (local dev loop)
3. OTLP + local Jaeger
4. Langfuse cloud
5. Troubleshooting

## 1. Picking an exporter

The exporter factory (`llm_code.engine.observability.exporters.build_exporter`)
dispatches on `ObservabilityConfig.exporter`:

| Value | When | Optional dep |
|-------|------|--------------|
| `"off"` | Disable tracing entirely. | — |
| `"console"` | Local dev: pretty-prints finished spans to stderr. | `rich` (optional; falls back to plain text) |
| `"otlp"` | Any OTLP collector (Jaeger, Tempo, Honeycomb, Datadog). | `opentelemetry-exporter-otlp-proto-http` (HTTP, default) or `-grpc` |
| `"langfuse"` | Hosted LLM observability. | `langfuse>=2.0.0` |

When the selected dep is missing the factory **logs a warning and
returns `None`** — engine boot continues without tracing rather
than failing.

## 2. Console (local dev loop)

`config.json`:

```json
{
  "engine": {
    "observability": {
      "enabled": true,
      "exporter": "console"
    }
  }
}
```

Run any agent command; finished spans render as a tree:

```
pipeline.DefaultPipeline           0.421s
 |- component.PermissionCheck      0.001s
 |- component.ToolExecutor[bash]   0.302s
 |- component.Postprocess          0.005s
api.stream[claude-sonnet-4]        0.110s
```

The exporter buffers until a span group is complete, then renders
the whole tree in parent-first order using Rich when available.
If `rich` is not installed it prints one line per span:

```
--- spans ---
pipeline.DefaultPipeline 0.421s
component.PermissionCheck 0.001s
...
```

Force an early flush (useful for long runs):

```python
from llm_code.engine.observability.exporters.console import ConsoleSpanExporter
exporter = ConsoleSpanExporter()
exporter.flush_tree()
```

## 3. OTLP + local Jaeger

### Step 1 — run Jaeger locally

```bash
docker run --rm --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  -p 4318:4318 \
  jaegertracing/all-in-one:1.56
```

- `16686` — Jaeger UI (open in a browser).
- `4317` — OTLP gRPC receiver.
- `4318` — OTLP HTTP receiver.

### Step 2 — install the OTLP exporter dep

```bash
pip install "opentelemetry-exporter-otlp-proto-http"
# or, for gRPC:
pip install "opentelemetry-exporter-otlp-proto-grpc"
```

### Step 3 — wire the config

```json
{
  "engine": {
    "observability": {
      "enabled": true,
      "exporter": "otlp",
      "otlp_protocol": "http/protobuf",
      "otlp_endpoint": "http://localhost:4318/v1/traces",
      "otlp_headers": [],
      "service_name": "llmcode-dev",
      "redact_span_attributes": true
    }
  }
}
```

Swap to gRPC by setting
`"otlp_protocol": "grpc"` and
`"otlp_endpoint": "http://localhost:4317"`. When the gRPC package
isn't installed the factory automatically falls back to HTTP (see
`otlp.py:build_otlp_exporter`).

### Step 4 — inspect traces

Navigate to `http://localhost:16686`, pick `llmcode-dev` in the
service dropdown, and open a trace. You should see
`pipeline.*` → `component.*` → `api.stream` + `tool.*` as nested
spans with GenAI-semantic attributes.

## 4. Langfuse cloud

### Step 1 — sign up + create a project

Go to [https://cloud.langfuse.com](https://cloud.langfuse.com) →
create project → **Settings → API Keys → Create new secret
key**. Copy both the public and secret keys.

### Step 2 — export env vars

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-...."
export LANGFUSE_SECRET_KEY="sk-lf-...."
# optional — self-hosted Langfuse:
export LANGFUSE_HOST="https://langfuse.internal.example.com"
```

### Step 3 — install the SDK

```bash
pip install "langfuse>=2.0.0"
```

### Step 4 — wire the config

```json
{
  "engine": {
    "observability": {
      "enabled": true,
      "exporter": "langfuse",
      "langfuse_public_key_env": "LANGFUSE_PUBLIC_KEY",
      "langfuse_secret_key_env": "LANGFUSE_SECRET_KEY",
      "langfuse_host": "https://cloud.langfuse.com",
      "service_name": "llmcode"
    }
  }
}
```

The exporter (`LangfuseSpanExporter`) maps llmcode spans onto
Langfuse's model:

- Spans with `gen_ai.*` attributes (i.e. API spans) become
  **generations** — token usage and model name land on the
  generation object.
- Spans with `llmcode.tool.name` become **tool** observations
  (level `ERROR` when `llmcode.tool.result_is_error = true`).
- Every other span becomes a generic **span**.
- `llmcode.session.id` attribute binds related spans into a
  Langfuse session for side-by-side trace comparison.

## 5. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `exporter 'otlp' requested but optional dep is missing` in the log | Install `opentelemetry-exporter-otlp-proto-http` (or `-grpc`). |
| Spans appear in the process but never in Jaeger | Endpoint mismatch: `http://localhost:4318/v1/traces` vs `http://localhost:4318`. The HTTP exporter wants the full `/v1/traces` path. |
| Langfuse returns `401` | Public / secret key pair is for a different project, or the env vars were swapped. |
| Console exporter prints nothing in tests | Spans buffer until each group is complete — call `exporter.flush_tree()` at test teardown or set `sampler=ALWAYS_ON`. |
| All spans appear as `[REDACTED]` | Expected — the span attribute redactor rewrote a value that matched one of the PII patterns. See `observability_redaction.md`. |

For exporter-specific bugs, the module docstrings in
`llm_code/engine/observability/exporters/` describe the exact
dependency surface each branch requires; imports are guarded so
adding a broken config is never fatal to engine boot.

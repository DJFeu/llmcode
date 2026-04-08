# Changelog

## Unreleased

### Added
- Agent decision tracing:
  - Telemetry.span(name, **attrs) — canonical context-manager primitive for
    nested spans (replaces the previous flat-root design)
  - Telemetry.trace_llm_completion(...) — opens an llm.completion span with
    prompt + completion previews (truncated to 4 KB), provider, finish reason
  - Optional Langfuse export: when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
    are set (env or config), spans are also forwarded to Langfuse alongside
    the OTLP exporter via langfuse.otel.LangfuseSpanProcessor
  - Each conversation turn is now wrapped in an agent.turn parent span; the
    LLM call and every tool call become children of that span, forming a
    tree visible in Jaeger / Langfuse / any OTel-compatible UI
  - langfuse>=3.0 added to the existing [telemetry] extra:
    pip install 'llm-code[telemetry]'

### Changed
- TelemetryConfig now has langfuse_public_key, langfuse_secret_key,
  langfuse_host fields. The config parser falls back to environment variables
  with the same names (uppercase) when the dict keys are absent.

### Refactored
- TelemetryConfig is now declared in exactly one place
  (llm_code/runtime/telemetry.py) and re-exported from
  llm_code/runtime/config.py for backward compatibility. Eliminates a
  duplicate dataclass that previously required manual field synchronization
  between the two copies and a duck-typed bridging block in tui/app.py.
- tui/app.py now passes RuntimeConfig.telemetry straight into Telemetry()
  instead of reconstructing it field by field. Adding a new TelemetryConfig
  field no longer requires three coordinated edits.

## v0.1.0 (2026-04-03) — Production Cleanup

### Changed

- Default CLI is now Ink UI (React/Node.js); use `--lite` for print-based fallback
- Updated `pyproject.toml` GitHub URLs from placeholder to `adamhung/llm-code`
- README updated: Ink UI default, `--lite`/`--serve`/`--connect`/`--ssh` flags documented, ClawHub marketplace, cost tracking, model aliasing

### Fixed

- `[send error:]` debug print in `ink_bridge.py` replaced with `logging.debug`
- Dead code: removed unused `removed` variable in `algorithms/gemma4_agent.py`
- Bare `except:` in `algorithms/gemma4_agent.py` replaced with `except Exception:`
- Unused `subargs` variable in `cli/tui.py` `/session` handler removed
- Semicolons on same-line imports in `ink_bridge.py` split to two statements
- Test `test_cli/test_image.py` updated: `detect_image_references` aliased to `extract_dropped_images`

### Removed

- `bubble_sort.py`, `multiplication.py`, `simple_demo.py` — development test artifacts
- `llm_code/algorithms/` directory — unreferenced Gemma4 agent prototype

### Chores

- Ruff lint: 39 issues fixed (34 auto-fixed, 5 manually resolved)
- All 1089 tests pass (3 skipped)

## v0.1.0 — Initial Release (2026-04-03)

### Features

**Core Agent (v1)**
- 6 built-in tools: read_file, write_file, edit_file, bash, glob_search, grep_search
- Multi-provider support: OpenAI-compatible API + Anthropic
- Dual-track tool calling: native function calling + XML tag fallback
- Streaming output with Rich Markdown rendering
- Layered permission system (5 modes + allow/deny lists)
- Hook system (pre/post tool use with exit code semantics)
- Session persistence and multi-session switching
- Layered config (user → project → local → CLI)
- Vision fallback for non-vision models
- Context compaction

**Smart Safety (v2)**
- Input-aware safety classification (bash ls = read-only, rm = destructive)
- Safety → permission system integration (dynamic effective_level)
- Pydantic runtime input validation
- Tool progress streaming via thread pool + asyncio.Queue

**Ecosystem (v3)**
- MCP client (stdio + HTTP transport, JSON-RPC 2.0)
- Plugin marketplace (5 registries: Official, Smithery, npm, GitHub, custom)
- Claude Code plugin.json compatibility
- Skills system (auto-inject + slash command trigger)
- Incremental streaming Markdown rendering
- Prefix cache optimization

**Agent Capabilities (v4)**
- Sub-agent tool (asyncio.gather parallel execution)
- Specialized agent roles (Explore, Plan, Verify)
- Model routing (static config + per-call override)
- Git-based undo/checkpoint (auto before writes)
- 7 git-aware tools with sensitive file detection

**Deep Integration (v5)**
- LSP integration (3 query tools + auto-detect)
- Cross-session memory (key-value + auto session summaries)
- Project indexer (file tree + regex symbol extraction)

**Production Quality (v6)**
- 4-level context compression (snip → micro → collapse → auto)
- Streaming tool execution (read-only tools execute during model output)
- Reactive compact (413 error recovery)
- Token budget and tool result budget
- MCP server instructions injection
- Structured logging
- Graceful shutdown
- Config validation
- GitHub Actions CI
- Docker support
- Documentation site

# Changelog

## Unreleased

### Fixed
- `LspClient._request` now uses an id-dispatch loop, correctly handling interleaved server notifications (`window/logMessage`, `$/progress`, etc.) and concurrent requests. Pre-existing latent bug exposed by the broader LSP coverage shipped in borrow-2/2.5.

### Changed
- `LspWorkspaceSymbolTool` rejects empty queries and caps results at 200 with a `(+N more)` tail.
- `LspWorkspaceSymbolTool` fans out across all running language clients (`asyncio.gather` + dedupe) instead of querying just the first.
- All LSP tools route inputs through a centralized `_validate_lsp_path` helper that returns clean `ToolResult(is_error=True)` for relative paths, missing files, or negative line/column.
- Sync-bridge boilerplate extracted to `_run_async` helper, deduplicated across 8 LSP tools.

### Added
- LSP coverage expansion ported from opencode:
  - `llm_code/lsp/languages.py` — single source of truth for extension→language
    mapping (~80 entries) and walk-up project root detection
  - `LspClient.hover()`, `document_symbol()`, `workspace_symbol()` methods with
    `Hover` and `SymbolInfo` dataclasses
  - Three new tools: `lsp_hover`, `lsp_document_symbol`, `lsp_workspace_symbol`
  - `detect_lsp_servers_for_file()` walks upward from any file to its project
    root before resolving servers
  - Expanded `SERVER_REGISTRY` covers 25+ language servers (up from 4)
- LSP call hierarchy + implementation:
  - `LspClient.go_to_implementation()` — concrete implementations of an
    interface, abstract method, or trait
  - `LspClient.prepare_call_hierarchy()` / `incoming_calls()` /
    `outgoing_calls()` — full callHierarchy/* surface
  - `CallHierarchyItem` dataclass with round-trippable LSP serialization
  - Two new tools: `lsp_implementation`, `lsp_call_hierarchy` (the latter
    accepts `direction: incoming | outgoing | both` and runs prepare →
    incoming/outgoing in one tool call)

### Fixed
- `CallHierarchyItem` now round-trips the original LSP node (`data`, `tags`,
  `range`, `selectionRange`, exact `kind` int) so servers like rust-analyzer
  and jdtls — which require their opaque `data` token to be echoed back —
  return non-empty incoming/outgoing call results. Unknown kind labels now
  raise instead of silently coercing to Function (12).
- `LspCallHierarchyTool` with `direction="both"` now dispatches incoming and
  outgoing calls concurrently via `asyncio.gather`, halving worst-case latency.
- `_CallHierarchyInput.direction` is now a `Literal["incoming","outgoing","both"]`
  so programmatic callers bypassing the JSON schema get Pydantic validation
  errors on bad values.

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

# Changelog

## Unreleased

### Added
- Agent tier routing (build / plan / explore / verify / general):
  - BUILD_ROLE (default, unrestricted) and GENERAL_ROLE (focused subagent
    without todowrite) added to BUILT_IN_ROLES
  - is_tool_allowed_for_role() helper — empty whitelist = no restriction
  - ToolRegistry.filtered(allowed) returns a child registry with only the
    named tools (parent untouched)
  - llm_code/runtime/subagent_factory.make_subagent_runtime() builds a
    role-filtered child ConversationRuntime with fresh Session and shared
    parent infrastructure
  - AgentTool is now actually wired — tui/app.py registers it with a
    lazy closure factory instead of runtime_factory=None
  - AgentTool.input_schema.role enum extended to all five roles

### Changed
- Agent role sentinel refactor: `AgentRole.allowed_tools` is now
  `frozenset[str] | None`. `None` means unrestricted (full inheritance);
  empty `frozenset()` is the explicit deny-all sentinel; non-empty set is a
  strict whitelist. `BUILD_ROLE.allowed_tools` is now `None`.
  `ToolRegistry.filtered(None)` clones the parent; `filtered(frozenset())`
  returns an empty registry. This eliminates the "empty set means
  unrestricted" foot-gun.

### Fixed
- AgentTool is no longer registered with a None runtime factory; calls now
  succeed instead of crashing on first dispatch.
- AgentTool recursion-depth guard now actually trips for build-role
  subagents. Previously, build-role children inherited the parent's
  AgentTool instance by reference, so `_current_depth` stayed at 0
  forever and `max_depth` was never enforced. `make_subagent_runtime`
  now rebinds the child's `agent` tool to a fresh AgentTool with
  `_current_depth = parent_depth + 1`.
- Defense-in-depth: `ConversationRuntime._execute_tool_with_streaming`
  now consults `is_tool_allowed_for_role` against the runtime's
  `_subagent_role` before dispatch, so a future regression that leaks
  a forbidden tool into a child registry still cannot bypass the role
  whitelist.

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

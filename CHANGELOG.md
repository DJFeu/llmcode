# Changelog

## Unreleased

### Fixed (hotfix — skill router false-match + thinking budget blowout)
- `skill_router` Tier C classifier: clean `none` answers are now authoritative and no longer fall through to the substring fallback. Fixes a regression where CJK queries auto-triggered an irrelevant skill (e.g. `brainstorming` for a news query) because reasoning models mention candidate skill names while ruling them out.
- `skill_router` Tier C substring fallback now requires ≥2 mentions of the winning skill AND a margin of ≥2 over the runner-up before accepting a match. A single mention in the reasoning block is no longer sufficient.
- `dynamic_prompt.build_delegation_section` now takes a `low_confidence` kwarg; when True (set when the routed skill came from the Tier C LLM classifier), the prominent `### Key Triggers` block is suppressed and skills appear only under the softer `### Skills by Category` block.
- `build_thinking_extra_body` now caps `thinking_budget` at `max(1024, max_output_tokens // 2)` when the provider exposes an output token limit, preventing thinking from consuming the entire visible response budget.
- `ConversationRuntime` now wires `_current_max_tokens` (the actual request `max_tokens`) into `build_thinking_extra_body` instead of probing for `provider.max_output_tokens` / `config.max_output_tokens` attributes that don't exist on the local OpenAI-compatible provider. The previous attribute probe always returned `None`, leaving the cap a no-op in TUI mode (which is how the bug was originally observed). Both call sites (initial request and XML-fallback retry) are fixed.
- **qwen.md system prompt: scoped "tool use is mandatory" to file/shell work only.** Previously the prompt instructed Qwen3 to always prefer tools, causing it to invent phantom tool calls (`bash curl` for an RSS feed) on conversational queries like "今日熱門新聞三則". The `<tool_call>` XML would then be stripped by the TUI and surface as an empty-response warning. Now the prompt explicitly says knowledge/explanatory/chit-chat queries must be answered directly. Verified against local Qwen3.5-122B: the same query now produces a clean 57-token direct answer with `has_tool_call=False`.
- **TUI empty-response diagnosis: distinguish `<tool_call>`-only turns from thinking-exhaustion.** The previous "thinking 用光輸出 token" message fired for any turn that emitted tokens but rendered no visible text. Now if the turn contained a `<tool_call>` XML block (which the TUI strips), the message instead tells the user the model tried to call a tool and suggests adding "請直接回答" to the prompt.
- **qwen.md: forbid mentioning tools that aren't actually available.** Even after the previous "tool use is for file/shell only" fix, the model was still suggesting "我可以使用 web_search 工具" in plain text — a tool that doesn't exist in llm-code. The follow-up turn where the user picked option 1 then triggered an actual `<tool_call>web_search` and the empty-response warning. New rule explicitly forbids mentioning or offering hypothetical tools; if the model can't help with the available tools, it must say so directly and stop.
- **TUI i18n: empty-response language detection now session-aware.** Previously the CJK detector only looked at the latest user input, so a Chinese user typing a short ASCII follow-up like `1` or `ok` would flip back to English. Now the helper walks recent user messages in the session and stays Chinese as long as any prior user turn contained CJK.

### Added
- Three themed builtin hooks ported from oh-my-opencode:
  - `context_window_monitor` — warns once per session at 75% context usage
  - `thinking_mode` — detects "ultrathink" / 深入思考 keywords and flags the turn
  - `rules_injector` — auto-injects CLAUDE.md / AGENTS.md / .cursorrules content
    when a project file is read
- `HookOutcome.extra_output: str` — allows in-process hooks to append content to
  the visible tool result (used by `rules_injector` and `context_window_monitor`).
- `context_window_monitor` builtin hook now actually fires — `ConversationRuntime`
  populates `_last_input_tokens` / `_max_input_tokens` after every LLM stream.
- `thinking_mode` builtin hook is now consumed — `_thinking_boost_active` doubles
  the next turn's `thinking_budget` (capped at provider max).
- Dynamic delegation prompt section: when the conversation runner has live
  tools and routed skills, the system prompt now includes an `## Active
  Capabilities` section with three subsections — Tools by Capability (grouped
  read/search/write/exec/lsp/web/agent), Key Triggers (skill triggers + names),
  and Skills by Category (grouped by skill's first tag). Pure module
  `llm_code/runtime/dynamic_prompt.py`. Byte-budget guard caps the section at
  8 KB by default to protect cache stability.
- Agent tier routing (build / plan / explore / verify / general):
  - BUILD_ROLE (default, unrestricted) and GENERAL_ROLE (focused subagent
    without todowrite) added to BUILT_IN_ROLES
  - is_tool_allowed_for_role() helper
  - ToolRegistry.filtered(allowed) returns a child registry with only the
    named tools (parent untouched)
  - llm_code/runtime/subagent_factory.make_subagent_runtime() builds a
    role-filtered child ConversationRuntime with fresh Session and shared
    parent infrastructure
  - AgentTool is now actually wired — tui/app.py registers it with a
    lazy closure factory instead of runtime_factory=None
  - AgentTool.input_schema.role enum extended to all five roles
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
- `LspWorkspaceSymbolTool` rejects empty queries and caps results at 200 with a `(+N more)` tail.
- `LspWorkspaceSymbolTool` fans out across all running language clients (`asyncio.gather` + dedupe) instead of querying just the first.
- All LSP tools route inputs through a centralized `_validate_lsp_path` helper that returns clean `ToolResult(is_error=True)` for relative paths, missing files, or negative line/column.
- Sync-bridge boilerplate extracted to `_run_async` helper, deduplicated across 8 LSP tools.
- Agent role sentinel refactor: `AgentRole.allowed_tools` is now
  `frozenset[str] | None`. `None` means unrestricted (full inheritance);
  empty `frozenset()` is the explicit deny-all sentinel; non-empty set is a
  strict whitelist. `BUILD_ROLE.allowed_tools` is now `None`.
  `ToolRegistry.filtered(None)` clones the parent; `filtered(frozenset())`
  returns an empty registry. This eliminates the "empty set means
  unrestricted" foot-gun.
- TelemetryConfig now has langfuse_public_key, langfuse_secret_key,
  langfuse_host fields. The config parser falls back to environment variables
  with the same names (uppercase) when the dict keys are absent.

### Fixed
- `rules_injector` no longer reads `CLAUDE.md` / `AGENTS.md` from ancestor
  directories outside the resolved project root (symlink edge case).
- `dynamic_prompt.build_delegation_section` no longer hangs under pathologically small `max_bytes` (added iteration cap + length-stable bailout)
- `dynamic_prompt.build_delegation_section` now honors `max_bytes` strictly — if even the bare header+intro envelope exceeds the budget, returns `""` instead of a soft-violating string
- `classify_tool` recognizes bare `Task` tool name as `agent` category (was falling through to `other`)
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
- `LspClient._request` now uses an id-dispatch loop, correctly handling interleaved server notifications (`window/logMessage`, `$/progress`, etc.) and concurrent requests. Pre-existing latent bug exposed by the broader LSP coverage shipped in borrow-2/2.5.
- Telemetry.span() outer guard restored: failures from the underlying OTel
  context manager (start_as_current_span enter / exit) no longer propagate
  to the caller, preserving the contract that "telemetry must never break
  the caller". Caller exceptions raised inside the with-block still
  propagate as before.
- llm.completion span no longer leaks if the XML tool-call fallback retry
  itself raises. The retry call site in Conversation._run_turn is now
  wrapped so any exception triggers _close_llm_span_with_error before
  propagating.

### Refactored
- _truncate_for_attribute is now imported at the top of conversation.py
  instead of lazily inside the post-stream enrichment block. Removes
  per-call import overhead and surfaces genuine import bugs.
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

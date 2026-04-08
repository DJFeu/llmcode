# Changelog

## v1.12.0 (2026-04-08)

**Highlights:**
- **Single source of truth refactor** (PR #21) — shared `ConversationRuntime` test fixture, canonical `StreamParser` replaces TUI + runtime duplicate parsers, system prompt ↔ ToolRegistry lint
- **Hermes variant 4 parser** (PR #22) — handles `<tool_call>NAME{"args": {...}}</tool_call>` with no `>` separator; StreamParser now emits sentinel event on unparseable blocks so TUI diagnostic is accurate
- **`-q` quick mode** now drives the real `ConversationRuntime` — no longer bypasses the code path it's supposed to smoke-test
- **Hermes fixture regression museum** grew to 4 captured variants

### Fixed (Hermes variant 4 + StreamParser sentinel)
- **`tools/parsing.py:_HERMES_FUNCTION_TRUNCATED_RE`** now handles Qwen3 variant 4, where the model emits `<tool_call>NAME{"args": {...}}</tool_call>` with no `>` separator between function name and JSON payload. Captured live from Qwen3.5-122B on 2026-04-08 as `tests/test_tools/fixtures/hermes_captures/2026-04-08-pr22-truncated-no-separator.txt`. 4 new unit tests + fixture replay coverage.
- **`streaming/stream_parser.py`** now emits a sentinel `TOOL_CALL` event (`tool_call=None`) when it consumes a `<tool_call>...</tool_call>` block whose body the downstream parser cannot understand. Previously the block was silently swallowed, which caused the TUI to fall back to the "thinking ate output" empty-response diagnostic instead of the "model tried to call a tool" message. New regression test pins this behavior.

### Refactored (single source of truth)
- **`tests/fixtures/runtime.py`** — shared `make_conv_runtime()` factory with canned-response provider and callback-based test tool. Runtime-level tests no longer hand-build a `ConversationRuntime` with ad-hoc `_Provider` classes. Unblocks the PR #17 Task 3 smoke tombstone (now a real test that proves Hermes-truncated tool calls get dispatched through the full runner).
- **`llm_code/cli/oneshot.py:run_quick_mode`** — `-q` quick mode now routes through the real `ConversationRuntime` via `run_one_turn`. Previously it called the provider directly, bypassing system prompt / tool registry / parser / dispatcher — which is why PRs #11/#13/#14 all "verified" fixes via `-q` that missed the real TUI-path bugs.
- **`LLMCodeTUI._register_core_tools_into(registry, config)`** — classmethod extracted from the TUI constructor so the oneshot path registers the same collaborator-free tool set (file/shell/search/web/git/notebook). Prevents the two paths from drifting.
- **`llm_code/streaming/stream_parser.py`** — canonical `StreamParser` state machine for `<think>` / `<tool_call>` parsing. Both TUI rendering and runtime dispatch consume the same events via `StreamParser.feed()`. The TUI inline parser (~110 lines of state machine) is replaced with 45 lines of event routing — net −63 lines and a single source of truth for what the model emitted. 14 unit tests cover text-only, think blocks (full and implicit-end), tool calls (all 3 Hermes variants), cross-chunk tag splits, interleaving, flush.
- **`tests/test_runtime/test_prompt_tool_references.py`** — lint test that scans `<!-- TOOL_NAMES: START -->` / `<!-- TOOL_NAMES: END -->` marker blocks in system prompt markdown files and asserts every backtick-quoted tool name exists in the `ToolRegistry`. Catches the PR #11 / #13 class of bug (system prompt contradicting actual registered tools) before merge.

## v1.11.0 (2026-04-08)

**Highlights:**
- 7 major features ported from oh-my-opencode (themed hooks, dynamic prompt delegation, agent tier routing, LSP coverage expansion, call hierarchy, telemetry tracing with Langfuse)
- Hermes function-calling parser that handles all 3 variants emitted by vLLM-served Qwen3 and similar tool-fine-tuned local models
- Tool-call resilience: fixture replay regression museum + idempotent retry loop detector
- `web_search` and `web_fetch` tools (already existed but now properly advertised in system prompt)

### Added (resilience hardening from 2026-04-08 bug hunt)
- `tests/test_tools/fixtures/hermes_captures/` — regression museum holding the verbatim model captures from PRs #14/#15/#16. `tests/test_tools/test_parsing_fixture_replay.py` parametrizes over the directory and asserts every capture parses; new captures land here as `.txt` files and are auto-discovered. Future parser refactors cannot silently break any of the three Hermes variants we've seen in production.
- `llm_code/runtime/_retry_tracker.RecentToolCallTracker` — per-turn idempotent retry detector. When the model emits the same `(tool_name, args)` pair twice in a row, the runtime aborts the turn with a clear error instead of looping. Closes the failure mode from 2026-04-08 where a parser bug caused web_search to be dispatched with empty args, fail validation, and burn 76K tokens / 3.6 minutes in a retry loop before giving up. 9 unit tests cover argument-order independence, nested dicts, recovery, and unhashable-arg defense.
- `tests/test_runtime/test_conversation_full_path_smoke.py` — tombstone for a future smoke test that exercises the conversation runner's parser path end-to-end with a fake provider. Currently skipped pending a `ConversationRuntime` test fixture; documents the gap so it can't be silently forgotten.

### Fixed (hotfix — Hermes truncated form with JSON args)
- `tools/parsing.py:_parse_hermes_block` now also handles a third
  Hermes sub-format: truncated function name followed by a JSON
  object payload instead of `<parameter=...>` blocks. PR #15 added
  the truncated form parser but only matched `<parameter=KEY>` blocks,
  so when the model emitted
  `<tool_call>web_search>{"args": {"query": "...", "max_results": 3}}</tool_call>`
  the parser extracted the function name `web_search` but returned
  empty args, causing the runtime to dispatch with empty args, fail
  validation, retry, and accumulate ~76K tokens in a 3.6-minute
  retry loop before giving up. New `_parse_hermes_args` helper tries
  parameter blocks first, then JSON payload (with optional `args` /
  `arguments` wrapper). 6 new TDD tests including the verbatim
  production capture. 37 / 37 parsing tests pass.

### Fixed (hotfix — Hermes template-truncated tool call format)
- `tools/parsing.py:_parse_hermes_block` now also handles the
  template-truncated form of Hermes function calls. Some chat templates
  (notably vLLM-served Qwen3 in tool-calling mode) inject
  ``<tool_call>\n<function=`` as the assistant prompt prefix, so the
  streamed body of `<tool_call>` starts directly with the bare function
  name (e.g. `web_search>...`) instead of `<function=web_search>...`.
  PR #14 added the full-form parser but did not handle this truncated
  variant; the parser silently dropped these calls and the runtime saw
  zero parsed tool calls, ending the turn with an empty visible reply.
  Captured live from local Qwen3.5-122B and pinned in TDD test
  `test_template_truncated_exact_capture_from_production`. 6 new tests
  cover single/multi/no params, underscore-name, full-form coexistence,
  and the malformed `<function>` literal that must still be skipped.
  31 / 31 parsing tests pass.

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
- **REAL ROOT CAUSE: `tools/parsing.py` now handles Hermes / Qwen3 function-calling format.** PR #11/#13 misdiagnosed the "今日熱門新聞三則 → empty response" bug as system-prompt-induced phantom tool calls. The actual root cause was that `_parse_xml` only accepted JSON-payload format `<tool_call>{"tool": "NAME", "args": {...}}</tool_call>`, while vLLM-served Qwen3 (and most tool-fine-tuned local models) emit Hermes function-calling format inside `<tool_call>` blocks: `<function=NAME><parameter=KEY>VALUE</parameter></function>`. The parser silently dropped these and the runtime saw 0 tool calls, ending the turn with no visible output. `_parse_xml` now tries JSON first, falls back to a Hermes block parser. 6 new TDD tests cover single/multi-param, no-param, multi-line content, mixed-format, malformed-block-skip, and multiple calls in one response.
- **qwen.md system prompt: reverted PR #11/#13 over-restriction.** With the parser fixed, the model can correctly use `web_search` and other read-only tools for legitimate conversational queries (news, weather, doc lookups). The new SP guidance: "use the right tool for the task" — `web_search` for real-time info, `web_fetch` for user-supplied URLs, `read_file`/`bash`/etc. for file/shell work, direct answer for pure knowledge queries. Still forbids inventing tools not in the registered list, and forbids `bash curl` for arbitrary URLs.

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

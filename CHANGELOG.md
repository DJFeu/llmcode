# Changelog

## Unreleased — Wave2-1c: Empty response counter + context pressure pre-warning

### Added
- **`_consecutive_empty_responses`** counter on `ConversationRuntime`. Empty turn (no text, no tool calls) → increment; productive turn → reset. **2nd in a row** injects a nudge user message (`[system nudge] Your previous response was empty...`); **3rd** raises `RuntimeError` so a degenerate provider state cannot burn the turn budget on nothing.
- **`empty_assistant_response` hook event** fires on every empty response with `{consecutive, model}`. Observers see the escalation unfold regardless of whether nudge/abort thresholds have been reached.
- **`context_pressure` hook event** fires once per ascending bucket transition **before** the 100% compaction trigger. Buckets: `low` (<70%), `mid` (70–85%), `high` (≥85%). Payload: `{bucket, ratio, est_tokens, limit}`. Compaction resets the bucket so the next ascending crossing re-fires.
- Both new event names in `_EVENT_GROUP`: `context.context_pressure` + `session.empty_assistant_response` so `context.*` / `session.*` glob subscribers pick them up automatically.

### Fixed
- Empty response loops silently burned turn budgets (the old `if assistant_blocks:` just skipped assembly with no logging or counter).
- Context-window pressure was invisible to observers until the 100%-hit compaction log — no pre-emptive escape hatch.

### Tests
- **`tests/test_runtime/test_wave2_1c_empty_context.py`** — 24 new tests: 3 hook registration, 10 pressure buckets (9 parametrized + zero-limit guard), 5 pressure transitions (ascending mid / mid→high, no spam within bucket, silent descent, refire after reset), 5 empty-counter state machine (continue/nudge/abort/reset/hook-on-every-empty), 1 source-level guard on runtime `__init__` sentinels.
- Full sweep: **1666 passed**, no regressions.

### Wave2-1 progress
| Sub | Status | PR |
|---|---|---|
| 1a P1–P5 thinking blocks | ✅ | #26–#30 |
| 1b Retry-After + ProviderTimeoutError | ✅ | #31 |
| **1c Empty counter + context pre-warn** | **✅** | **this** |
| 1d CancelledError cleanup | — | — |

## Unreleased — Wave2-1b: Retry-After header + ProviderTimeoutError

### Added
- **`ProviderRateLimitError.retry_after: float | None`** field carries the provider's `Retry-After` header value (in seconds) when the 429 response included one. Downstream `_post_with_retry` now honors this hint instead of always using `2 ** attempt`, so the retry respects the provider's own rate-limit reset window.
- **`ProviderTimeoutError`** — new retryable `ProviderError` subclass wrapping `httpx.ReadTimeout` / `ConnectTimeout` / `WriteTimeout` / `PoolTimeout`. Previously all four flavors fell through `_post_with_retry` uncaught and became generic `Exception` in the conversation loop, skipping the retry budget entirely. Now they get the standard exponential backoff path just like `ProviderConnectionError`.
- **`_parse_retry_after_header(raw)`** helper in `openai_compat.py` — defensive parser that accepts the delta-seconds form (every real LLM provider's 429 response), returns `None` on missing / empty / unparseable / non-positive / HTTP-date input, and **clamps positive values to `_MAX_RETRY_AFTER_SECONDS = 60.0`** so a misbehaving proxy returning `Retry-After: 86400` cannot wedge the runtime for a day.

### Fixed
- **`_post_with_retry` split `ProviderRateLimitError` off from `ProviderConnectionError`.** The combined handler used `2 ** attempt` for both; now rate-limit specifically checks `exc.retry_after` and falls back to exponential only when absent. Connection errors are unchanged.
- **`_raise_for_status` reads `Retry-After` from the 429 response** and passes it to the new `ProviderRateLimitError(msg, retry_after=...)` constructor.

### Tests
- **`tests/test_api/test_rate_timeout_backoff_wave2_1b.py`** — 13 new tests:
  - 5 unit tests for `_parse_retry_after_header`: None/empty, delta-seconds (int + float + whitespace), unparseable (garbage + HTTP-date form), non-positive rejection, 60s cap clamp
  - 4 rate-limit retry tests: honors `Retry-After: 3.5`, falls back to `2 ** attempt` without header, clamps hostile `999999` to 60s, exhausted budget re-raises with `retry_after` attribute preserved
  - 3 timeout tests: `httpx.ReadTimeout` → retry, `ConnectTimeout` → retry, all 4 flavors exhausted → `ProviderTimeoutError(is_retryable=True)`
  - 1 sanity test: 401 auth error still not retried (verifies wave2-3 `is_retryable` path is untouched)
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1655 passed**, no regressions.

### Context
Part of the wave2-1 session recovery follow-through (see `docs/superpowers/specs/2026-04-08-llm-code-borrow-wave2-audit.md`). The audit found:
- RateLimited ⚠️: no exponential backoff respected header, no Retry-After parsing — **fixed**
- ProviderTimeout ⚠️: no special handling, timeouts fell through generic Exception catch — **fixed**

Remaining wave2-1 items: **1c** (EmptyAssistantResponse counter + ContextWindow pre-warning), **1d** (CancelledError cleanup on interrupted tool execution).

## Unreleased — Wave2-2: Cost tracker cache tokens + unknown-model warning

### Fixed
- **`TokenUsage` now carries `cache_read_tokens` / `cache_creation_tokens`** end-to-end. Previously the streaming provider parser dropped both buckets on the floor when building `TokenUsage`, so even though `CostTracker.add_usage()` already supported the 10% / 125% cache-pricing math, the TUI hook had nothing to feed it. Cache reads on claude-sonnet-4-6 are roughly 10% of input price, so a session doing heavy prompt caching was over-billed by the full cache-read amount in every summary.
- **`llm_code/api/openai_compat.py`** centralizes usage-dict → `TokenUsage` conversion in `_token_usage_from_dict()`, which handles both payload shapes: OpenAI-compat nests cache reads under `prompt_tokens_details.cached_tokens`; Anthropic surfaces them as top-level `cache_read_input_tokens` / `cache_creation_input_tokens`. Anthropic's explicit field wins when both appear.
- **`llm_code/tui/app.py` `StreamMessageStop` hook** now forwards the cache buckets into `cost_tracker.add_usage(cache_read_tokens=..., cache_creation_tokens=...)`. Uses `getattr(..., 0)` so any stray `TokenUsage` constructed without the new fields stays safe.
- **`CostTracker` warns once per unknown model.** Self-hosted setups (Qwen on GX10 etc.) still stay silent after the first event, but a genuine typo in the model name now surfaces with `cost_tracker: no pricing entry for model 'xxx'; treating as free. Add a custom_pricing row in config if this is a paid model.` — previously it silently priced the whole session at $0. Empty model name is also silent so initialization ordering doesn't spam the log.

### Tests
- **`tests/test_runtime/test_cost_tracker_wave2_2.py`** — 11 new tests: TokenUsage backward-compat defaults, OpenAI vs Anthropic usage-dict extraction (including the "both shapes present" edge case), empty-dict handling, warn-once / warn-per-new-model / known-model-silent / empty-model-silent, and end-to-end cache pricing (`claude-sonnet-4-6`: 1M cache_read + 1M cache_write = $4.05).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1660 passed** (up from 1653, no regressions).

## Unreleased — Wave2-3: Model fallback quick-win fixes

### Fixed
- **`llm_code/runtime/conversation.py` provider error handler** now short-circuits on `is_retryable=False` errors (`ProviderAuthError`, `ProviderModelNotFoundError`). Previously a 401/404 from the upstream API burned the full 3-strike retry budget before the fallback switch, wasting time and quota on errors that cannot possibly succeed on retry. A new `http_non_retryable` hook fires so observers can count these distinctly from transient failures.
- **`cost_tracker.model` now follows a fallback switch.** When the 3-strike threshold flips `self._active_model` to the fallback model, the runtime also assigns `self._cost_tracker.model = _fallback` and resets `_consecutive_failures`. Previously every token after a fallback was still priced as the (failed) primary model, so session cost summaries mis-attributed spend. `_consecutive_failures` used to stay at 3 after the switch, which meant the new model got zero retries before the next escalation — that's now reset to 0 on switch.

### Tests
- **`tests/test_runtime/test_fallback_wave2_3.py`** — 7 new tests pin the two fixes: non-retryable error contract on `ProviderAuthError`/`ProviderModelNotFoundError`, retryable contract on rate-limit/overload, default retryable behavior for bare exceptions, writable `cost_tracker.model`, and end-to-end pricing attribution across a model switch (verifies the tracker uses the new custom-pricing row after reassignment).
- Full conversation + retry-tracker regression sweep (37 tests) still passes.

## Unreleased — Wave2-4: Compaction todo preserver + phase-split hooks

### Added
- **`pre_compact` / `post_compact` hook events.** Observers can now distinguish the snapshot moment from the rehydration moment of a compaction pass. The legacy `session_compact` event still fires alongside `pre_compact` so existing hook configurations keep working unchanged. Both new events are in the canonical `session.*` group, so any glob subscriber (e.g. `session.*`) picks them up automatically.
- **`llm_code/runtime/todo_preserver.py`** — pure module providing `snapshot_incomplete_tasks(task_manager)` (best-effort, never raises even on a broken task store) and `format_todo_reminder(snapshot, max_tokens=500)` with a hard token cap. The formatter truncates with a `... (N more)` footer when the cap would be exceeded, so a runaway task list cannot balloon an already-tight context window.
- **`ConversationRuntime._compact_with_todo_preserve(max_tokens, reason)`** helper routes all four in-tree compaction call sites (proactive / prompt_too_long / api_reported / post_tool) through a single path that fires the phase-split hooks with uniform payload: `{reason, before_tokens, target_tokens, preserved_todos}`. Previously only one of the four sites fired `session_compact` at all, so observers had no visibility into three of the compaction triggers.

### Tests
- **`tests/test_runtime/test_todo_preserver_wave2_4.py`** — 12 new tests covering: empty/broken/None task-manager handling, snapshot structure, format hard-cap truncation with `... (N more)` footer, default-cap sanity for typical sessions, phase-event registration in `_EVENT_GROUP`, and `session.*` glob matching for both new phase events.
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1654 passed**, no regressions.

## Unreleased — Wave2-5: Plugin executor (schema + dynamic loader + SkillRouter hooks)

### Added
- **`PluginManifest.provides_tools`** — declarative list of Python tools a plugin exports as `"package.module:ClassName"`. Parses from either `providesTools` (camelCase) or `provides_tools` (snake_case).
- **`PluginManifest.permissions`** — declared capability envelope (dict). Wave2-5 reads for surfacing / audit; sandbox enforcement is a follow-up. Non-dict values dropped defensively.
- **`llm_code/marketplace/executor.py`** — the missing piece. `load_plugin(manifest, install_path, *, tool_registry, skill_router=None, force=False)` resolves each `provides_tools` entry, imports the module (with install path temporarily on `sys.path`, restored in `finally`), instantiates the class, registers it. Returns a `LoadedPlugin` handle so `unload_plugin` can reverse the load. `PluginLoadError` / `PluginConflictError` carry `.plugin_name` + `.entry` for log-traceable failures.
- **`ToolRegistry.unregister(name) -> bool`** — idempotent removal. Used by executor rollback and `unload_plugin`.
- **`SkillRouter.add_skill(skill)`** / **`remove_skill(name) -> bool`** — post-construction registration/removal. Rebuilds TF-IDF + keyword index, invalidates route cache, rejects duplicate names.

### Fixed
- **Plugin-provided Python tools now have an actual loader.** Before wave2-5 the marketplace had manifest parsing + install-from-local/github/npm + security scan + 91 tests, but no code path that took a declared tool class and put it in the tool registry. Plugin authors could ship Python tools and llm-code silently ignored them.

### Contract: rollback on any failure
Any failure during `load_plugin` (unparseable entry / missing module / missing class / ctor failure / name conflict) unregisters every tool this load call already registered before the exception propagates. Registry returns to its pre-load state. Pinned by `test_load_plugin_rolls_back_on_conflict` — a two-tool plugin whose second tool conflicts leaves the first tool NOT registered.

### Scope discipline
Lands the **executor + schema + router hooks only**. TUI wiring (hooking `load_plugin` into `_cmd_plugin install` and `_reload_skills`) is deferred to a follow-up PR. Existing `/plugin install` path for markdown-only skill plugins continues to work exactly as before.

### Tests
- **`tests/test_marketplace/test_plugin_executor_wave2_5.py`** — 20 new tests: 6 manifest schema (camelCase / snake_case / empty / permissions dict / default None / non-dict dropped), 3 `unregister` (remove / missing / re-register), 3 happy-path (fixture plugin loads, empty manifest, sys.path cleanup), 2 conflict (rollback / force override), 4 structural failures (unparseable / missing module / missing class / broken ctor), 2 `unload_plugin` (removes / idempotent)
- **`tests/test_runtime/test_skill_router_add_remove_wave2_5.py`** — 7 new tests: add grows list, add rejects duplicate, add invalidates cache, remove unknown returns False, remove works, remove invalidates cache, add-then-remove round-trip
- Full `tests/test_runtime/` + `tests/test_api/` + `tests/test_marketplace/` + `tests/test_tools/` sweep: **2794 passed**, no regressions (existing 91 marketplace tests unchanged).

### Wave2 status: all 11 items landed

| Item | PR |
|---|---|
| wave2-1a thinking blocks P1–P5 | #26–#30 |
| wave2-1b rate-limit + timeout | #31 |
| wave2-1c empty + context pre-warn | #32 |
| wave2-1d cancel cleanup | #33 |
| wave2-2 cost tracker | #24 |
| wave2-3 fallback | #24 |
| wave2-4 todo preserver | #25 |
| wave2-6 dialog launcher | #34 |
| **wave2-5 plugin executor** | **this** |

## Unreleased — Wave2-6: Dialog launcher (API + Scripted + Headless)

### Added
- **`llm_code.tui.dialogs` package** with unified `Dialogs` Protocol (4 async methods: `confirm` / `select` / `text` / `checklist`), generic `Choice[T]` frozen dataclass (`value`, `label`, `hint`, `disabled`), and two explicit exception types (`DialogCancelled`, `DialogValidationError`).
- **`ScriptedDialogs`** deterministic test backend. Pre-enqueue responses via `push_confirm` / `push_select` / `push_text` / `push_checklist` / `push_cancel`. `.calls` log captures exact prompt text; `assert_drained()` at teardown catches unconsumed responses. Validates enqueued select / checklist values are actually in the passed-in choice list.
- **`HeadlessDialogs`** stdin/stderr line-based backend for CI, pipe mode, `--yes` runs, SSH without TTY. Writes prompts to stderr so piped stdout stays clean. Multi-line text is blank-line terminated. Select uses 1-based indices. Checklist parses comma-separated indices. EOF / out-of-range / disabled / non-integer → `DialogCancelled`. `assume_yes=True` short-circuits every prompt to its default with zero I/O. `confirm(danger=True)` renders a ⚠ prefix.

### Scope discipline
This PR lands the **API + two non-interactive backends only**. The Textual backend (modal screens inside the running app) and the call-site migration sweep (~12 existing hand-rolled prompts across `llm_code/tui/`) are deferred to follow-up PRs so this change stays focused and reviewable:

- No existing TUI code is modified — every hand-rolled prompt continues to work exactly as before.
- New code that needs a dialog can already use `ScriptedDialogs` in tests and `HeadlessDialogs` in CI.

### Tests
- **`tests/test_tui/test_dialogs_wave2_6.py`** — 36 new tests:
  - 4 Protocol surface + `Choice` type tests
  - 13 `ScriptedDialogs` tests (push/empty-queue/cancel, value membership validation, validator runs, bounds enforcement, drain assertion, call log)
  - 16 `HeadlessDialogs` tests (confirm y/n/blank/EOF/danger, `assume_yes` short-circuit, select index/default/out-of-range/disabled, text single/default/multiline/validator, checklist comma/blank/min/max)
  - 3 cross-backend contract tests (shared `_drive_simple_confirm` helper exercises both backends against the same spec)
- Full `tests/test_runtime/` + `tests/test_api/` + `tests/test_tui/` sweep: **2008 passed**, no regressions.

### Deferred
- `TextualDialogs` backend (needs screen push/pop integration)
- Call-site migration sweep (worktree confirm, permission prompt, skill picker, commit-message input, settings modal, quick-open, MCP approval, etc.)
- Removal of legacy prompt helpers after migration


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

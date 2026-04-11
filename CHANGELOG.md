# Changelog

## v1.21.0 — Local Whisper, Recovery Pass, CHANGELOG Backfill

This release is the "loose ends" cut: every deferred Wave2 item from the
2026-04-11 deep-check gets shipped, the CI matrix stops warning on
deprecated Node actions, and the CHANGELOG finally covers the four
intermediate releases (v1.16 → v1.18.2) that had no entry before.

### New Features
- **`/voice` backend: `local`** — embedded `faster-whisper` inference, no HTTP server required. Set `voice.backend = "local"` in config and optionally pick a model size with `voice.local_model` (`tiny` / `base` / `small` / `medium` / `large-v3`). Weights download lazily on first `/voice on` into the faster-whisper cache, so config-time cost is zero. New pip extras: `pip install llmcode-cli[voice-local]` pulls `sounddevice>=0.5` + `faster-whisper>=1.0` together. The factory now accepts four backends: `local` / `whisper` / `google` / `anthropic`.
- **Wave2-1a thinking_order recovery** — new `llm_code.runtime.recovery.thinking_order` with `repair_assistant_content_order(blocks, mode="reorder"|"strip")`. Partitions `ThinkingBlock` instances to the front of the content tuple without modifying their signature bytes (required for Anthropic extended thinking verbatim round-trip). `"strip"` mode drops any late thinking block for callers that already invalidated signatures. Sibling of the existing `api/content_order.py` validator — validator raises, recovery repairs. 12 unit tests cover well-ordered / out-of-order / signature preservation / mode selection.
- **Wave2-3 telemetry `record_fallback`** — `Telemetry.record_fallback(from_model, to_model, reason)` emits an `llm.fallback` OTel span with `llm.fallback.{from,to,reason}` attributes. Called from `ConversationRuntime` at the same site as the `http_fallback` hook so external tracing backends (Jaeger / Honeycomb / Tempo) can chart fallback-chain walks without parsing logs. Disabled / no-package paths remain no-op.
- **Wave2-2 cost_tracker round-trip** — `CheckpointRecovery.load_checkpoint(session_id, *, cost_tracker=...)` and `detect_last_checkpoint(cost_tracker=...)` now restore the tracker's running token and cost totals from the checkpoint, instead of silently dropping them on reload. `save_checkpoint` already embedded the payload; only the load path was missing. The `/checkpoint resume` command passes the live cost tracker through so a resumed session continues cost accounting from where it left off. 5 new checkpoint round-trip tests cover legacy-checkpoint compatibility.

### Fixed
- **Voice error string: package name** — `detect_backend()` raised "Install sounddevice (`pip install llm-code[voice]`)" on failure, but the actual PyPI package is `llmcode-cli`. The hyphenated name would not resolve on PyPI; now emits `pip install llmcode-cli[voice]`.
- **`/voice` guard copy** — the "voice not configured" message now lists all four backends (`local`, `whisper`, `google`, `anthropic`) and points at `config.json` instead of a non-existent `config.toml`.

### Infrastructure
- **CI actions upgraded to Node 24** — `actions/checkout@v4` → `v5`, `codecov/codecov-action@v4` → `v5`. Both ship the Node 24 runtime so GitHub's 2026-06 deprecation warnings stop firing on every run. `actions/setup-python@v5` stays until v6 releases (still Node 20 upstream).

### Docs
- **CHANGELOG backfill** — added full entries for v1.16.0, v1.16.1, v1.17.0, v1.18.0, v1.18.1, and v1.18.2. The file previously jumped from v1.15.1 to v1.19.0 with no coverage of the architecture refactor, VS Code extension, 6-phase agent upgrade, gold-gradient logo, `/update` command, `/theme` command, or centralized tool registry work.

### Tests
- **5268 passing** (+25 vs v1.20.0): 12 thinking_order recovery, 2 telemetry record_fallback (noop + mocked-otel), 5 checkpoint cost_tracker round-trip, 6 LocalWhisperSTT (protocol / lazy load / missing-dep error / mocked pipeline / factory routing), unchanged Pydantic + dialog 3.10+ compat.

---

## v1.20.0 — Prompt History, Right-Arrow Autocomplete, Python 3.10+ Floor

### New Features
- **Shell-style prompt history** — submitted prompts are stored in `~/.llmcode/prompt_history.txt` (oldest-first so `tail` shows the newest) with bash / zsh `HISTCONTROL=ignoredups` semantics: consecutive duplicates collapse, empty entries are dropped, and the list is capped at 1000 entries with the oldest evicted first. In the InputBar, **↑ walks older** submissions, **↓ walks newer**, and stepping past the newest entry restores the composing draft you had before you started navigating. History is suppressed when the slash-command dropdown is open (those arrows are already claimed for dropdown nav), inside vim mode (j/k/gg/G own up/down there), and for multi-line buffers (arrow = cursor movement). Any keystroke or delete resets the history cursor so you can freely edit a recalled prompt.
- **Right-arrow accepts dropdown completion** — when the `/`-command autocomplete dropdown is visible, `→` now commits the highlighted command just like `Enter` / `Tab`. The dropdown only appears before a space is typed, so there is no legitimate cursor-right movement to preempt.

### Fixed / Compat
- **Python 3.10+ is now the real floor** (was briefly advertised as 3.9+ in v1.19.0, but 3.9 couldn't actually run the codebase). Three independent 3.9 breakages turned up: module-level `TextValidator = Callable[[str], str | None]`, `ModalScreen[str | None]` class bases, and — the unfixable one — `asyncio.Queue() / Event() / Lock()` eagerly binding to the event loop inside sync `__init__` in `mcp/transport.py`, `lsp/client.py`, `runtime/tool_pipeline.py`, and several tests. Python 3.10 made those primitives lazy-bind, so 3.10 is the minimum. CI matrix, `requires-python`, classifiers, ruff `target-version`, README, and `docs/getting-started.md` all aligned on 3.10+. The CI matrix now covers `["3.10", "3.11", "3.12", "3.13"]` contiguously (the earlier skip of 3.10 was a typo).
- **v1.19.0 `/voice`, `/help`, `/export`, HookDispatcher, FallbackChain, Phase 5 consolidations** — all the commits from v1.19.0 land in this release on top of a CI run that actually passes. (v1.19.0's CI was red against a Python 3.9 cell that could not load the codebase; the tag still exists but should be considered superseded.)

### Tests
- **5243 passing** (+15 vs v1.19.0): the 15 new tests are for `PromptHistory` — in-memory semantics, consecutive dedup, max_entries bound, persistence round-trip, oldest-first file layout, missing/unreadable file handling, cursor reset on edit, draft restore on ↓ past newest.

## v1.19.0 — Architecture Refactor Finish, /voice Wire, /help Modal Rewrite, Py3.9 Compat

### New Features
- **Declarative FallbackChain** — `ModelRoutingConfig.fallbacks: tuple[str, ...]` with a stateless `FallbackChain.next(current, error_kind)` API. The legacy single-shot `fallback: str` is promoted to a 1-element chain so existing configs keep working. Non-retryable errors (auth, model-not-found, 413) short-circuit so they don't consume fallback budget. Enables chains like `sonnet → haiku → gpt-4o → local`.
- **`/voice` actually works** — the command was a dead stub that only flipped `_voice_active`; no code ever imported `voice.recorder` or `voice.stt`. Now wires the full pipeline: `/voice on` detects a recording backend (sounddevice / sox / arecord), builds an `STTEngine` from config, and starts capture. `/voice off` stops the recorder and runs transcription in `asyncio.to_thread`, then inserts the text into the InputBar on the UI thread. Bare `/voice` shows status, backend, and language.
- **`/export` implementation** — writes the live conversation to a Markdown file via a new `_render_session_markdown` helper that walks `session.messages` for every block type (text, thinking, tool_use, tool_result, image, server_tool_use, server_tool_result). Thinking blocks collapse in `<details>` for GitHub. `/export` defaults to `./llmcode-export-<id>-<date>.md`; `/export <path>` takes an explicit target. The command was declared in the registry but had no handler before this release.
- **Python 3.9 / 3.10 support** — the advertised `python>=3.9` now actually runs. Audited 320 `llm_code/*.py` + 434 `tests/*.py`: no `match`/`except*`/runtime PEP 604 unions/`TaskGroup`/`ParamSpec`. The single 3.11+ blocker was `tomllib` in `model_profile._load_toml`; now falls back to the `tomli` package (same API) on older interpreters. `pyproject.toml` declares `tomli>=2.0; python_version < "3.11"`.

### Fixed
- **`/help` modal was double-broken** — `_refresh_content` funneled a `RichText` through `Console(force_terminal=True)` to an ANSI string and fed it to `Static.update()`, but Textual's `Static` does not decode ANSI. Escape bytes became literal characters, garbling margins and breaking height math. Separately, the list tabs tracked a `>` cursor marker inside a single `Static`, so the surrounding `VerticalScroll` had no idea where the cursor was and silently dropped `down`/`PageDown`/`End` — only the first ~13 commands were ever reachable. Rewrote `HelpScreen` to use Textual's built-in `OptionList` for the commands / custom-commands tabs; keyboard nav, scrollbar, focus highlight all work natively. Verified headless with `pilot.press("down") × 50 + end + home`: `highlighted` and `scroll_y` track correctly across all 52 options.
- **4 slash commands had no autocomplete hint** — `/update`, `/theme`, `/cache`, `/personas` had working `_cmd_*` handlers but no `CommandDef` in `COMMAND_REGISTRY`. They ran if typed in full but never appeared in the `/` dropdown, Tab-completion, or `/help`. Added registry entries.
- **`/export` was a dead hint** — the opposite drift: registry declared it but dispatcher had no `_cmd_export`, so selecting `/export` resolved to "Unknown command". Implemented (see above).

### Architecture refactor (2026-04-11 plan — 100% complete)
- **Phase 2.1** — `HookDispatcher` extracted from `conversation.py`. `_fire_hook` becomes a thin delegator; the ~26 call sites inside `conversation.py` (pre_compact, prompt_submit, http_fallback, …) don't need to change.
- **Phase 5.3** — `voice/*.py` (7 files, ~366 LOC) consolidated into `tools/voice.py`. Old package kept as backward-compatibility shims so `tests/test_voice/` passes unchanged.
- **Phase 5.4** — `sandbox/docker_sandbox.py` + `pty_runner.py` consolidated into `tools/sandbox.py`. `bash.py`, `tui/app.py`, `runtime/config.py` point at the canonical location; old `sandbox/` package stays as shim.
- **Phase 5.5** — `hida/{types,profiles,engine,classifier}.py` (4 files) consolidated into `runtime/hida.py`. 50-test `tests/test_hida/` suite untouched thanks to shim layer.

### Tests
- **5228 passing** (+20 vs v1.18.2): 6 HookDispatcher, 9 FallbackChain, 6 voice wire-up, 7 `/export` markdown renderer.
- `test_dispatcher_has_all_52_commands` now derives expected names from `COMMAND_REGISTRY` at runtime instead of a hard-coded list.
- New `test_registry_has_no_dead_handlers` enforces the opposite direction: every `_cmd_*` must have a registry entry. Prevents both drifts (dead hints, missing hints) from recurring.

### Docs / CI
- Complete 52-command reference table added to `README.md` as a collapsible `<details>` block after the Terminal UI highlight list.
- CI matrix filled in to `["3.9", "3.10", "3.11", "3.12", "3.13"]` — the earlier `["3.9", "3.11", …]` skipped 3.10.

## v1.18.2 — Architecture Refactor Round 1 (app.py/conversation.py decomposition)

### Refactor — large-file decomposition
- **`app.py` 3999 → 1200 lines** — extracted `CommandDispatcher` (51 `_cmd_*` methods), `StreamingHandler` (430-line `_run_turn`), and `RuntimeInitializer` (440-line `_init_runtime`) into dedicated modules under `tui/`.
- **`conversation.py`** — extracted `PermissionManager` and `ToolExecutionPipeline`, each with a well-defined collaborator boundary.
- **`runtime/memory/` unified** — `KVMemoryEntry` rename + lint merged into validator.
- **`config.py` split** — feature submodules (701 → 611 lines); enterprise/vision/voice configs now live in `config_features.py`, `config_enterprise.py`, `config_migration.py`.
- **`enterprise/` → `runtime/enterprise.py`** — auth / RBAC / OIDC / audit logger collapsed into a single module.
- **`streaming/` → `tui/stream_parser.py`** — `stream_parser.py` moved to its only consumer.
- **Tool consolidation** — `swarm_*.py`, `task_*.py`, `cron_*.py` tool wrappers (10 files) merged into `tools/swarm_tools.py`, `tools/task_tools.py`, `tools/cron_tools.py`.
- **Centralized tool registry** — `tools/registry.py` + `tools/builtin.py`; adding a new tool is now a single `CommandDef`-style registration.

### Features
- Wired 6 previously-orphan modules into runtime: `agent_loader`, `tool_visibility`, `tool_distill`, `prompt_snippets`, `denial_parser`, `exec_policy`.

### Fixed
- Source-inspection tests updated to the new file locations.
- Ruff F401 / TYPE_CHECKING regressions introduced by the refactor.

---

## v1.18.1 — `/update` Command + 8 Built-In Themes

### Features
- **`/update` command** — checks PyPI for a newer version, shows current → latest, and runs `pip install --upgrade llmcode-cli` in-place. Startup banner performs a cached background check (6-hour TTL) so the user sees an update hint without manual polling.
- **8 built-in themes** — dracula, monokai, tokyo-night, github-dark, solarized-dark, nord, gruvbox, plus the original default. Switch with `/theme <name>`.

### Docs
- README comparison table gained Codex CLI + Gemini CLI columns.

---

## v1.18.0 — Codex / Gemini CLI Patterns + Local Model Recovery

### Features
- **7-phase Codex / Gemini CLI design adoption** — imported patterns from the upstream CLIs (permission staging, tool-output shaping, retry triage) into llmcode's conversation loop.

### Fixed
- **Local model retry recovery** — when a local LLM retry path previously aborted on malformed tool results, the tool results are now preserved on the next iteration.
- **Text-only iteration after tool results** — local models that don't handle tool/text interleaving well now force a text-only follow-up iteration instead of looping on the same tool call.

### Docs
- Competitor list updated: replaced Continue.dev (IDE assistant, not a CLI agent) with actual CLI-agent peers.

---

## v1.17.0 — 6-Phase Agent System + Logo Refresh + TUI Scroll Fix

### Features
- **6-phase agent system upgrade** — borrowed from `claude-code`: tiered filtering, fork-cache, frontmatter agents, memory scopes, contextvars, worktree isolation.
- **Gold gradient logo** — TUI welcome banner + README SVG now share a pixel-perfect gold gradient rendering via Rich's `export_svg()`. Several iterations on block-art preservation (keep original font; swap gradient only; rect pixels for SVG; bust GitHub camo cache with a filename change).

### Fixed
- **TUI scroll regression** — addressed as part of the agent-system rework.
- **Local model tool nudge** — small models drop tool calls when the system prompt is too long; now get a short nudge.
- Ruff F401 unused-import lint in test files.

---

## v1.16.1 — Model Tuning Bump

Version bump only — carries the v1.16.0 model-profile feature set to PyPI.

---

## v1.16.0 — Model Profile Tuning, Dream Consolidation, VS Code Extension

### Features
- **Per-model profile tuning** — temperature, reasoning effort, and small-model auto-downgrade now live on the `ModelProfile` so llmcode can adapt the same conversation to radically different backends without config churn.
- **4-stage dream consolidation** — `DreamManager` gains trigger guards, date normalization, and memory pruning so the "sleep" consolidation pass doesn't run on empty sessions or re-process the same window.
- **Cache breakpoint detection** — Anthropic prompt-cache breakpoint lookup and placement, plus anti-recursive sub-agent spawn (prevents a `task` tool invocation from immediately dispatching the same tool again).
- **Circuit breaker** — `ConversationRuntime` stops retrying after 3 consecutive compact failures instead of spinning forever on an unrecoverable prompt-too-long loop.
- **VS Code extension scaffold** — bridge + chat panel + code actions + WebSocket client + status bar. Full extension source under `extensions/vscode/`. Code actions include "Ask about selection" and "Fix with llmcode".

### Security
- **Path case normalization + SSRF defenses** — added port blocking and DNS rebinding defense to the `web_fetch` / `web_search` path; path normalization prevents case-insensitive bypass of permission allowlists on macOS / Windows.

### Fixed
- CI failures around `ParsedToolCall.args`, pair-integrity checks, test-count badge drift.

### Docs
- i18n comparison corrected — CJK support is partial, not full.
- IDE extensions added to the vs-other-tools comparison table; rows re-ordered.
- Qwen Code added to the comparison table.
- VS Code extension design spec (bridge + chat panel + code actions).

---

## v1.15.1 — SSE Streaming, Docker Sandbox, PTY, Plan Mode Tools, Arena Pattern

### New Features
- **AnthropicProvider real SSE streaming** — `_AnthropicLiveStreamIterator` reads events via httpx `aiter_lines()` as they arrive, instead of downloading the entire response first
- **Docker sandbox** — `DockerSandbox` class with Docker/Podman auto-detection, container lifecycle, and `SandboxConfig` (image, network, memory/CPU limits). Wired into `BashTool._run()` as optional isolation layer
- **PTY runner** — `run_pty()` via ptyprocess for interactive commands (git rebase -i, etc.) with optional pyte screen rendering. `BashTool` gains `pty: true` input parameter
- **Plan mode tools** — `enter_plan_mode` / `exit_plan_mode` tools let the model control plan→act transitions programmatically
- **Arena pattern** — `AgentBackend` Protocol + `ArenaManager` for parallel agent coordination with pluggable backends (subprocess, tmux, worktree)
- **Profile TOML hot-reload** — `ProfileRegistry.reload_if_changed()` stats directory mtime, called automatically from `get_profile()`
- **Marketplace search** — filter input, category grouping headers, stats bar

### Fixed
- **Scroll regression** — reverted all experimental scroll changes (watch_scroll_y, priority bindings, key_* handlers, InputBar dispatch) back to v1.15.0 baseline. Shift+Up/Down, PageUp/PageDown, and mouse wheel (Warp native) all work correctly again

### Tests
- 11 new tests: ChatScrollView auto-scroll, permission dialog choices, settings write-back validation, edit-args encoding

## v1.15.0 — Profile System Phase 2, Prompt Caching, Mouse Scroll, 11 TODO Resolutions

### Profile System Deep Wiring
- **StreamParser reads `implicit_thinking` from model profile** instead of probing config.thinking.mode
- **`build_thinking_extra_body()` branches on profile format** — `anthropic_native` (Anthropic) vs `chat_template_kwargs` (vLLM/OpenAI-compat)
- **Local model detection reads `profile.is_local`** with URL-pattern fallback for unknown models
- **SkillRouter tier-C model reads from profile** → config → active model (3-level fallback)
- **`/model` displays profile info** — capabilities, provider type, pricing, context window
- **Profile auto-discovery** — probes `/v1/models` at runtime to match better profiles for unknown model names
- **TOML example profiles** in `examples/model_profiles/` (qwen3.5-122b, claude-sonnet, custom-local)

### Anthropic Provider Enhancements
- **Prompt caching** — automatic `cache_control: ephemeral` on system prompt, last tool definition, and last user message content block. Adds `anthropic-beta: prompt-caching-2024-07-31` header.
- **Signature delta accumulation** — `StreamThinkingSignature` event carries the complete cryptographic signature from streaming `signature_delta` events, wired through to `ThinkingBlock` for round-trip.
- **Server tool use blocks** — new `ServerToolUseBlock` / `ServerToolResultBlock` types with signature round-trip. Streaming parser assembles them from `content_block_start/stop` events.

### Streaming & Parsing Fixes
- **Accept mismatched XML closing tags** — Qwen3.5 sometimes emits `<web_search>JSON</search>` (truncated closer). Variant 5 regex now accepts any `</identifier>` as closer.
- **Strip trailing XML tags from JSON body** — the Hermes args parser now removes any trailing `</tag>` before JSON parsing, fixing empty-args bug.
- **StreamParser bare tool detection** — accepts `known_tool_names` and classifies `<tool_name>JSON</tag>` as TOOL_CALL during streaming, preventing raw XML from appearing in chat.

### TUI Improvements
- **Mouse wheel scrolling** — scroll up pauses auto-scroll so you can browse history during streaming; scroll to bottom resumes. Fixed `resume_auto_scroll()` being called on every text chunk.
- **Permission prompt → TextualDialogs modal** — replaced inline y/n/a key handler with `select()` dialog
- **MCP approval → TextualDialogs modal** — replaced inline key handler with async modal dialog
- **Edit args** — new "Edit args" option in permission dialog; opens text editor for JSON, sends modified args to runtime
- **`/set` command** — live config write-back (`/set temperature 0.5`, `/set max_tokens 8192`, `/set model ...`)
- **Removed dead `PermissionInline` import**

### Plugin System
- **Fixed `_tool_registry` → `_tool_reg` bug** — plugin tools were never actually loading due to wrong attribute name
- **Plugin unload wiring** — `_unload_plugin_tools()` called on disable/remove, handles stored in `_loaded_plugins` dict
- **`env` added to dangerous permissions** — blocks plugins requesting environment variable access unless `--force`
- **Skill file loading from manifests** — executor now loads SKILL.md files from `manifest.skills` into SkillRouter

### Runtime
- **Memory distillation at startup** — `distill_daily()` runs at TUI init (today-\*.md → recent.md → archive.md)
- **Subagent per-role model routing** — `model` parameter in `make_subagent_runtime()` now creates a config override
- **Settings write-back** — `apply_setting()` validates and applies changes via `dataclasses.replace`

### TODO Cleanup
- Updated 6 stale TODO/follow-up comments to reflect completed wiring (MCP agent_approval, MCP server_registered, memory distillation cron, plugin permissions)

## Unreleased — perf: SkillRouter negative cache + timing log (cuts Tier C overhead in half)

### Fixed
- **`SkillRouter.route_async` ran Tier C twice per turn** on CJK queries. The method is called from two places: `tui/app.py:1426` (for display) and `runtime/conversation.py:1036` (for prompt injection). Negative Tier C results (LLM classifier returned "no match") were never cached, so both call sites ran the full 5-15s LLM classifier round-trip. Observed in a 2026-04-09 field report as "Routing Skill 花了不少時間".
- **Fix**: `route_async` now checks the cache at the very top (BEFORE calling the sync `route()` helper) and caches the Tier C negative result explicitly. Second call within the same turn is a cache hit → instant return.
- **Additional fix**: the sync `route()` already cached empty results from Tier A/B misses, but `route_async`'s old code called `self.route()` (which returned `[]` from the cache), saw `if result:` as False, and **re-entered the Tier C path instead of honoring the cache**. The new top-level cache check covers this edge case too.

### Added
- **Debug logging for all tier decisions** so the user can see which tier matched and how long it took:
  - `skill_router cache hit: N skills in 0.000s` — cache hit short-circuit
  - `skill_router tier_a: N skills in 0.001s` — keyword match
  - `skill_router tier_b: N skills in 0.012s` — TF-IDF match
  - `skill_router tier_c starting: model=X cjk=True` — LLM classifier fire
  - `skill_router tier_c complete: matched='alpha' in 4.23s` — classifier result
  - `skill_router tier_c miss (negative cached): 4.23s total` — negative cached
- **`last_tier_c_debug`** now includes the elapsed time so `/skill debug` shows exact classifier cost.

### Impact
Same turn with a CJK query that triggers Tier C:
- **Before**: Tier C fires twice per turn (once for TUI display, once for prompt). If classifier takes 5s, that's **10s of overhead per turn**.
- **After**: Tier C fires once, cached. Second call is a map lookup (~µs). **Saves 5-10s per CJK turn**.

Combined with PRs #41/#42/#43/#44, a Qwen3.5 CJK turn's wall-clock now looks like:
- ~0-14s native fallback (only on first-ever session, cached after)
- ~4s XML iteration 1 (tool_call)
- ~5-10s Tier C (reduced from 10-20s)
- ~19s web_search execution
- ~21s iteration 2 (synthesis)
- **Total: ~50-55s first session, ~35-45s every session after**

### Tests
- **`tests/test_runtime/test_skill_router_negative_cache.py`** — 6 new tests:
  - Tier C negative result cached: second call doesn't re-invoke provider (core fix)
  - Tier C positive result cached (regression guard for pre-existing behavior)
  - Different queries get independent cache entries
  - No provider configured → Tier C skipped, empty cached, second call instant
  - Tier A hit → cached for async reuse (Tier C never fires)
  - Edge case: sync `route()` cached empty from Tier A/B miss is honored by `route_async`
- Existing 7 `test_skill_router_add_remove_wave2_5.py` tests + `test_skill_router_cjk_fallback.py` tests still pass
- Full sweep: **3272 passed**, no regressions

## Unreleased — perf: persistent native-tools capability cache (14s/turn → 0s after first)

### Added
- **`llm_code/runtime/server_capabilities.py`** — tiny persistent JSON cache at `~/.llmcode/server_capabilities.json` keyed by `(base_url, model)`. Records whether each server+model combination supports native OpenAI-style tool calling. When the `conversation.py` auto-fallback branch detects the "Server does not support native tool calling" error and sets `self._force_xml_mode = True`, it now also writes the result to this cache.
- **Next session reads the cache** at turn setup. If the combo is marked unsupported, `self._force_xml_mode` is seeded to True immediately and the entire 14-second native-rejection round-trip is SKIPPED on turn 1.

### Impact
The 14-second server-side latency that remained after PRs #41/#42/#43 is now paid **once per (server, model) combination, EVER** — not once per session. A user who runs llmcode daily against the same local vLLM server pays the 14s on day 1 and never again.

### Data model
```json
{
  "http://localhost:8000|/models/Qwen3.5-122B-A10B-int4-AutoRound": {
    "native_tools": false,
    "cached_at": "2026-04-09T13:30:00+00:00"
  }
}
```

Keyed by `f"{base_url.rstrip('/')}|{model}"` — trailing-slash-normalized base URL + exact model name. Two models on the same server get independent entries; same model on two servers gets independent entries. A future retention policy can expire stale entries via the `cached_at` timestamp.

### Atomic writes
Writes go through `tempfile.mkstemp` + `os.replace` so a concurrent reader never sees a partial write. Failed writes log at DEBUG and are swallowed — this is a pure optimization, not a correctness boundary.

### Cache management
- **Default**: write-on-fallback, read-on-turn-setup. No user action required.
- **Manual clear**: `clear_native_tools_cache()` wipes the whole file; `clear_native_tools_cache(base_url, model)` removes one entry. Exposed as a module-level helper for tests and a future `/cache clear` user command.

### Tests
- **`tests/test_runtime/test_server_capabilities.py`** — 14 new tests covering:
  - Load returns None on fresh system (no cache file)
  - Mark-then-load round-trip
  - Different models on same server are independent
  - Different base URLs with same model are independent
  - Trailing slash normalization on `base_url`
  - Marking one entry preserves siblings
  - Corrupted JSON file treated as "no cache" (never crashes)
  - Cache entries have ISO-format `cached_at` timestamp
  - `clear_native_tools_cache(url, model)` removes specific entry
  - `clear_native_tools_cache()` (no args) wipes entire cache
  - Partial clear (one arg) raises ValueError
  - Atomic writes leave no `.tmp` files
  - Source-level guard: `conversation.py` calls `load_native_tools_support`
  - Source-level guard: `conversation.py` calls `mark_native_tools_unsupported` in fallback branch
- Full sweep: **3266 passed**, no regressions

### Combined with the previous 8 fixes
| Metric | Original | After #41-#43 | **After this** |
|---|---|---|---|
| First turn / new server | 65s (retry storm) | 58s (1 fallback) | **58s** (one-time cost) |
| Second turn+ / same server | 58s | 58s | **~44s** (skips 14s) |
| Fifth session / same server | 58s × 5 = 290s | 58s × 5 = 290s | **44s + 58s (first) = 102s** for equivalent workload |

### Future enhancements
- `/cache clear` user command to manually reset if the user intentionally changes server config
- TTL-based expiry (e.g. 7 days) so a server that added `--enable-auto-tool-choice` is re-probed automatically
- Probe on startup rather than on first turn so even the first turn is instant

## Unreleased — fix: idempotent-retry detector actually aborts the turn (was: continue → burn 91s)

### Fixed
- **The `"Aborting turn: idempotent retry loop detected"` log message was a lie.** The code at `conversation.py:L1641` used `continue` inside the inner dispatch loop, which only skipped the offending call. The outer iteration loop kept running; the model got another turn, saw the `"Aborted"` error block, and re-emitted the same failing tool call on iteration N+1. Repeated until `max_turn_iterations` exhausted.
- **Field report 2026-04-09**: user's query burned **91 seconds** with the log line firing 3 times and input tokens bloating to **45,732** as the model re-emitted the same web_search call across iterations. The "abort" was purely cosmetic.
- **Fix**: replace the `continue` with `break` (exit the inner dispatch loop) AND set a new `_turn_aborted_by_retry_loop` flag checked at the end of each outer iteration. When the flag is set, the outer loop `break`s, the error `tool_result_block` is appended to the session for message history consistency, and a visible `StreamTextDelta` explains WHY the turn ended so the user isn't left wondering.

### Visible user message
Previously: silent loop burning turn budget, no explanation in the chat.

Now:
```
⚠ Aborted: the model asked to call 'web_search' again with the same
arguments as the previous call, which indicates a retry loop. Try
rephrasing your request, or check whether the tool result was useful.
```

### Tests
- **`tests/test_runtime/test_idempotent_retry_abort.py`** — 4 new source-level regression guards:
  - `test_idempotent_retry_uses_break_not_continue` — scans the retry-detected branch for `break` keyword (the exact 91s bug)
  - `test_turn_loop_breaks_on_idempotent_retry_flag` — `_turn_aborted_by_retry_loop` flag is checked in the outer iteration loop
  - `test_idempotent_retry_emits_visible_explanation` — user sees a ⚠ warning, not a silent abort
  - `test_retry_tracker_still_created_per_turn` — lifecycle unchanged (per-turn, not per-iteration)
- Existing `test_conversation_retry_loop_abort.py` (2 tests pinning the tracker unit) + force_xml + fallback tests all still pass
- Full sweep: **3252 passed**, no regressions

### Context
This is the 7th fix in a row chasing the Qwen3.5-122B TUI field report thread:
1. #36 — empty response diagnostics
2. #37 — truncation warning + stop_reason
3. #38 — flush() silent drop
4. #39 — parser variant 5
5. #40 — log-file flag (enabled clean log capture for further debugging)
6. #41 — force_xml sticky + retry storm (broke fallback ordering)
7. #42 — fallback ordering fix
8. **this** — idempotent retry actually aborts

Each one was a distinct root cause discovered by logs from the previous fix. The diagnostic-first discipline from #36/#37/#40 paid off repeatedly.

## Unreleased — fix: tool-call-parser fallback must run BEFORE is_retryable short-circuit

### Fixed
- **Regression from PR #41**: the tool-call-parser error is now marked `is_retryable=False` (correctly — it can't be fixed by re-sending the same request), but the `conversation.py` outer exception handler checked the wave2-3 `is_retryable is False` short-circuit **before** the XML-fallback branch. Result: the recoverable error bypassed its recovery path and surfaced to the user as visible assistant text — `"Error: 'auto' tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set"`. Reported immediately after PR #41 merged.
- **Fix**: reorder the branches in the outer `except Exception` block so the tool-call-parser string-match runs FIRST (rebuilds request without tools, retries in XML mode), and the `is_retryable is False` short-circuit runs SECOND as a fallback for genuinely unrecoverable errors (401 auth, 404 model not found).

### Conceptual distinction
The root cause was conflating two meanings of "retryable":
1. **Can retry the same request** (rate limit, timeout, transient failure) — wave2-3's `is_retryable=False` is about this
2. **Can recover from this error somehow** (retry as-is, switch mode, rebuild request) — tool-call-parser is recoverable via mode switch, not re-send

The fix makes the order explicit: try the specific recovery path first, fall through to the general "give up" check only if no recovery applies.

### Tests
- **`tests/test_runtime/test_force_xml_sticky.py`** — 2 new source-level regression guards:
  - `test_fallback_branch_runs_before_is_retryable_short_circuit` — pins the ordering by searching for the two relevant string positions in the method source
  - `test_is_retryable_short_circuit_still_present` — guards wave2-3's 401/404 behavior (the short-circuit was moved, not removed)
- Existing 4 force_xml guards + 16 wave2-3 fallback tests + 16 wave2-1b retry tests all still pass
- Full sweep: **3248 passed**, no regressions

### Impact
Same query that hit the "Error:" wall now triggers the XML fallback on the first attempt and produces a real tool_call. Combined with PR #41's fast-fail retry skip, the total turn time drops from 65s → estimated ~12s AND the turn actually succeeds instead of showing an error.

## Unreleased — perf: kill the native-tool-call retry storm (53s → ~0s)

### Fixed
- **Stale `force_xml` local** in `ConversationRuntime._run_turn_body` shadowed `self._force_xml_mode` within a single turn. The local was captured once at turn setup (L834) and never refreshed, so when iteration 1 hit the "tool-call-parser not supported" error and set `self._force_xml_mode = True`, iteration 2 still read the stale local as `False` and re-attempted native tool calling from scratch. Observed in a Qwen3.5 field report: second iteration burned ~19s on a duplicate retry storm before hitting the same fallback branch.
- **Tool-call-parser error is now non-retryable** in `_raise_for_status`. Previously a server response containing "tool-call-parser" or "tool choice" in the error message was classified as a generic `ProviderConnectionError` and went through `_post_with_retry`'s 3-strike exponential backoff — burning ~30s before the error surfaced to the outer fallback branch. Now it raises `ProviderError(msg, is_retryable=False)` which bypasses the retry loop entirely; the outer `except Exception` in `conversation.py` still pattern-matches the message and switches to XML mode on the first attempt.

### Impact
Same field-report turn: **65s total → estimated ~12s** (rough estimate; actual wins depend on server first-token latency for the legitimate XML-mode attempts).

Before:
- 12:52:19 Starting turn
- 12:52:53 Native fallback triggered (**+34s** — 3 native retries)
- 12:52:57 Executing web_search
- 12:53:16 Native fallback triggered **AGAIN** (**+19s** — duplicate retry storm)
- 12:53:24 Turn complete (**+8s**)

After:
- Native attempt → instant fail → immediate XML fallback (no retry sleeps)
- force_xml sticky → iteration 2 skips native entirely

### Tests
- **`tests/test_runtime/test_force_xml_sticky.py`** — 4 source-level guards pinning the fix:
  - `force_xml = getattr(self, ...)` local shadow pattern is gone
  - `use_native` reads `self._force_xml_mode` directly
  - `self._force_xml_mode` is initialized before the iteration loop
  - The fallback branch still sets `self._force_xml_mode = True` (wave2-3 regression guard)
- **`tests/test_api/test_rate_timeout_backoff_wave2_1b.py`** — 3 new tests for the fast-fail:
  - `"tool-call-parser"` error in response → `ProviderError(is_retryable=False)`, **zero sleeps**, **exactly 1 HTTP call**
  - `"tool choice"` error variant → same fast-fail behavior
  - Plain 400 without tool-related message → still retryable (preserves existing 4xx handling)
- Full sweep `test_runtime/` + `test_api/` + `test_tui/` + `test_streaming/` + `test_tools/`: **3246 passed**, no regressions.

### Analysis chain
The user ran `llmcode -v --log-file /tmp/llmv.log` (PR #40) and shared a clean log. Timeline analysis revealed:
1. Two "Server does not support native tool calling" messages per turn — obvious smell
2. 34s for first native-mode attempt → 3× retry in `_post_with_retry`
3. 19s for second iteration's retry of the same error → stale local caching `force_xml` at turn start

Without PR #40's log-file support this would have been impossible to diagnose — the user's earlier `2> /tmp/log` attempts garbled both the TUI and the log.

## Unreleased — cli: `--log-file` flag so `-v` doesn't break the TUI

### Added
- **`--log-file PATH`** CLI flag routes verbose logs to a file instead of `sys.stderr`. Required when running the TUI with `-v` — otherwise the user's natural instinct to do `llmcode -v 2> /tmp/log` interleaves Python logging output with Textual's own stderr writes, which completely breaks the TUI rendering (terminal fills with raw ANSI escape codes mixed with log lines).
- **`LLMCODE_LOG_FILE` environment variable** as the secondary source for the log destination, so users who want logs everywhere can set it once in their shell rc instead of passing the flag on every invocation.
- **Destination priority**: explicit `--log-file` > `LLMCODE_LOG_FILE` env > `sys.stderr` (existing default). Tilde expansion is honored so callers can pass `~/.llmcode/logs/debug.log`. Parent directories are created on demand.
- **`setup_logging(verbose, log_file)`** now accepts the new kwarg. When a log file is chosen, a `FileHandler` is installed and the `StreamHandler(sys.stderr)` is NOT — the TUI's stderr stream stays clean.

### Context
Found while investigating the Qwen3.5 TUI field reports. The user tried `llmcode -v 2> /tmp/llmv.log` to capture a verbose log for me to diagnose slowness — the command started the TUI but stderr redirect grabbed Textual's terminal control codes along with the log messages, producing a garbled log file and a broken TUI display ("卡住很久了"). The log file contained full TUI frame snapshots in ANSI escape sequences instead of clean log lines. No amount of user education can fix this — the right answer is a first-class file destination for logs that bypasses stderr entirely.

### Tests
- **`tests/test_logging_file.py`** — 8 new tests:
  - Default destination is stderr (pre-existing behavior preserved)
  - Explicit `log_file` argument installs a FileHandler, not StreamHandler
  - Messages actually land in the file (write + read roundtrip)
  - Parent directory auto-created so `~/.llmcode/logs/today.log` just works
  - `LLMCODE_LOG_FILE` env var used when no explicit arg
  - Explicit arg overrides env var
  - `~` path expansion honored
  - `verbose=False` still accepts log_file (destination is independent of level)
- Full sweep `test_logging_file.py` + `test_runtime/` + `test_api/` + `test_tui/` + `test_streaming/` + `test_tools/`: **3247 passed**, no regressions.

### Usage
```bash
# Previously broken:
llmcode -v 2> /tmp/llmv.log          # TUI garbled, log polluted with ANSI

# Now works cleanly:
llmcode -v --log-file /tmp/llmv.log  # TUI clean, log is just log lines
LLMCODE_LOG_FILE=/tmp/llmv.log llmcode -v  # same, via env var
```

## Unreleased — parser: recognize bare ``<NAME>JSON</NAME>`` tool call variant (Hermes variant 5)

### Fixed
- **Third Qwen3.5 field report fix**: user asked "今日新聞三則" and the TUI showed the raw text `<web_search>{"query": "今日熱門新聞", "max_results": 3}</web_search>` as the assistant's visible response. The tool never executed; iteration 2 never happened. Root cause: vLLM's chat template was producing a **bare `<NAME>JSON</NAME>`** tool-call format (tool name IS the XML tag, no `<tool_call>` wrapping) that none of the four existing Hermes variants in `parse_tool_calls` could match. With the parser returning empty, `runtime/conversation.py:L1564` broke out of the turn loop on `if not parsed_calls: break`, and the 22 output tokens of raw tool_call syntax became the visible reply.
- **New Hermes variant 5** — `_HERMES_BARE_NAME_TAG_RE` matches `<?([a-zA-Z_]\w*)>\s*(\{.*?\})\s*</\1>` with the leading `<` optional (some terminal renderings and prompt-prefix injections drop it). Only tried when no `<tool_call>` wrapper matched in `_parse_xml`, so the fast path for well-formed emissions is untouched. Handles three arg-nesting shapes: flat `{...}`, nested `{"args": {...}}`, nested `{"arguments": {...}}`.
- **False-positive guards**:
  1. **JSON validation**: the body must parse as a JSON object (scalars / lists / invalid JSON rejected).
  2. **Reserved names blocklist**: `tool_call`, `think`, `function`, `parameter` are never interpreted as variant 5 even when the body is valid JSON — prevents double-parsing of malformed `<tool_call>{"args": {}}</tool_call>` as a tool named `tool_call`.
  3. **`known_tool_names` registry gate**: `parse_tool_calls` now accepts an optional set of registered tool names; variant 5 only matches when the tag name is in the set. `runtime/conversation.py` passes `{t.name for t in self._tool_registry.all_tools()}` so production mode is strict. Tests without a registry pass `None` for permissive matching (documented caveat: `<p>{"a":1}</p>` would otherwise match in permissive mode).
- **`runtime/conversation.py:L1431`** now threads `known_tool_names` through to `parse_tool_calls` so the bare variant only fires on real tool names.

### Tests
- **`tests/test_tools/test_parsing.py::TestBareNameTagVariant`** — 13 new tests covering:
  - Exact field-report text parsed correctly
  - Missing leading `<` handled (terminal artifact / prefix injection)
  - Variant inside mixed prose
  - Multi-line body with newlines before/after JSON
  - Nested `"args"` key unwrapped to flat args
  - Nested `"arguments"` key unwrapped to flat args
  - `known_tool_names` blocks `<p>{"a":1}</p>` false positive
  - Invalid JSON rejected
  - Mismatched close tag rejected
  - Scalar / list body rejected
  - Reserved `tool_call` name NOT reinterpreted (regression guard for `test_missing_tool_key_skipped`)
  - Reserved `think` name NOT reinterpreted
  - Variant 5 does NOT fire when a valid `<tool_call>` wrapper is already present (no duplicate parses)
  - Multiple bare tool calls in one text each parse separately
- Existing 42 parsing tests still pass — **55 total** in that file
- Full sweep `test_tools/` + `test_runtime/` + `test_tui/` + `test_streaming/` + `test_api/`: **3239 passed**, no regressions

### Investigation chain
Field-report progression:
1. **Screenshot 1** (24 out tokens, empty response) → PR #36 added empty-response counter + unclassified variant message
2. **Screenshot 2** (279 out tokens, items vanished after intro) → PR #37 added stop_reason capture + truncation warning; PR #38 fixed `StreamParser.flush()` silently dropping unterminated `<tool_call>` content
3. **Screenshot 3** (22 out tokens, raw tool_call syntax as visible text) → **this PR**: the earlier PRs surfaced enough context to identify a third, distinct bug — the parser didn't recognize Qwen3.5's `<NAME>JSON</NAME>` variant at all

Each fix addressed a distinct root cause; none of them overlap.

## Unreleased — StreamParser flush: salvage unterminated tool_call instead of silent drop

### Fixed
- **Critical data loss bug**: `StreamParser.flush()` used to silently drop buffered content when the stream ended while inside an unterminated `<tool_call>` block. This matched exactly one field report: user asked "今日新聞三則", TUI showed 3 tool-call dots + "根據搜尋結果,以下是今日三則熱門新聞:" intro, and nothing else — despite the model reporting 279 output tokens. The news items were being generated by the model but got swallowed by a never-closed `<tool_call>` opening marker in the stream, and `flush()` threw them away at end-of-stream with zero diagnostic. Reproduced locally against a bare StreamParser; fix verified with the same repro.
- **`flush()` now salvages unterminated `<tool_call>` content as a TEXT event** instead of dropping it. The leading `<tool_call>` marker is stripped so the text reads naturally in the chat widget. Empty salvage (only the marker, no body) emits no event so the TUI doesn't show a blank assistant reply.
- **`flush()` also logs a warning** (`"unterminated <tool_call> block, salvaging N chars as TEXT"`) so `-v` runs capture the event. Silent data loss is worse than loud data loss.
- **Unterminated `<think>` block handling is preserved** (already emitted buffered content as THINKING before this fix) but now also logs a warning for symmetry.

### Tests
- **`tests/test_streaming/test_stream_parser.py`** — 7 new tests:
  - Unterminated `<tool_call>` body salvaged as TEXT with marker stripped
  - Full user scenario: intro + unclosed tool_call wrapping 3 news items — all items recoverable
  - Complementary: unterminated `<think>` content still preserved as THINKING (regression guard)
  - Edge case: empty buffer after `<tool_call>` marker emits no TEXT event
  - State is cleared after flush (`_in_tool_call=False`, `_buffer=""`) so parser is reusable
  - Warning log fires on `<tool_call>` salvage
  - Warning log fires on `<think>` salvage
- Existing 16 `test_stream_parser.py` tests still pass — **23 total** in that file
- Full `tests/test_streaming/` + `tests/test_tui/` + `tests/test_runtime/` + `tests/test_api/` sweep: **2191 passed**, no regressions.

### Root cause analysis
The investigation: user's screenshot showed 279 output tokens but only the intro line was visible. Oneshot `-q` mode worked fine for the same query (280 tokens, full 3-item list rendered), isolating the bug to the TUI's stream-rendering path. Walked the StreamParser source, identified three suspicious code paths (`flush()` drops, implicit-think-end race, state leak across iterations). Wrote targeted repro tests for each. **Scenario G** (unterminated `<tool_call>` → silent drop) matched the symptom exactly and reproduced on a bare StreamParser with no TUI / runtime / provider mocking.

### Related
- Follow-up to PR #36 (empty-response diagnostics) and PR #37 (stop_reason capture + truncation warning), which added the *visibility* to see this class of bug. This PR fixes an actual data-loss bug those surfaced.
- Complementary: Scenario A in the investigation revealed that implicit-think-end (bare `</think>` after text-that-was-already-emitted-as-TEXT) also has a design bug where content already streamed as TEXT cannot be retroactively reclassified as THINKING. That's a separate, less critical issue and not addressed here.

## Unreleased — TUI stop_reason capture + truncation warning

### Added
- **`LLMCodeTUI._last_stop_reason`** now captured at every `StreamMessageStop`. Previous PR referenced it but nothing assigned it — the value was always `"unknown"`. Initialized in `__init__` for first-turn safety.
- **Explicit truncation warning** rendered as a dedicated `AssistantText` entry when `stop_reason in ("length", "max_tokens")` AND some visible content was already shown (so the empty-response fallback didn't fire). Previously runtime's auto-upgrade path caught most cases but a provider that caps hard mid-stream let truncated turns through silently. New text:
  - ZH: `(⚠ 回應被截斷 — 模型達到輸出上限 (length)。實際輸出 279 tokens。試試加長 max_tokens 或 context window,或重新提問。)`
  - EN: `(⚠ Response truncated — the model hit its output token cap (length) after 279 tokens. Try increasing max_tokens / context window or rephrasing.)`
- **`_truncation_warning_message()`** pure helper. Reuses `_session_is_cjk`; testable without mounting the TUI.
- **Unconditional turn-end debug log** captures `out_tokens`, `thinking_len`, `assistant_added`, `saw_tool_call`, `stop_reason` on EVERY turn — not just empty-response path. `-v` runs now have full state for every turn, not just fallback paths.

### Context
Found by investigating a second Qwen3.5-122B screenshot: TUI showed 3 `web_search` dots + "根據搜尋結果,以下是今日三則熱門新聞:" intro but NO list items, despite model reporting 279 output tokens. Oneshot `-q` produced the full 3-item list for the same query. Isolated to TUI observability gap — the runtime already auto-upgrades on `finish_reason=length` (conversation.py:L1400-1409), but when that path doesn't catch it (e.g. partial-stream truncation), the TUI had no way to surface the cause.

### Tests
- **6 new tests** in `test_empty_response_i18n.py`: EN + ZH truncation warnings with token count, `max_tokens` stop_reason variant, zero-token edge case, CJK language detection from session history, ⚠ marker in both locales
- Existing 32 tests still pass — **38 total**
- Full `tests/test_tui/` sweep: **378 passed**, no regressions

### Not changed
- Runtime layer — purely TUI observability
- Runtime auto-upgrade on `finish_reason=length` — still fires first
- Empty-response fallback (PR #36) — still fires when no visible content

## Unreleased — Empty-response diagnostics: debug log + unclassified variant

### Added
- **Diagnostic log at the TUI empty-response fallback** captures the full state in one `logger.warning` line so a `-v` run has everything needed to debug the cause: `out_tokens`, `thinking_len`, `saw_tool_call`, `assistant_added`, `stop_reason`, and a 120-char `thinking_head` preview. Previously the user only saw a generic i18n message with no observable state — the only way to investigate was to hand-instrument and re-run.
- **New "unclassified tokens" diagnostic variant** (`_EMPTY_RESPONSE_UNCLASSIFIED_EN` / `_ZH`) for the specific case where the model emitted N output tokens but the TUI could not route *any* of them to visible text, thinking, or a dispatched tool call. Includes the actual token count in the message so the user can compare against their `max_tokens` / `thinking_budget` config without leaving the TUI. Classic causes: malformed `<think>` tags that slipped past the parser, a partial `<tool_call>` that got stripped but not dispatched, or truncation from a low output-token cap.
- **`_empty_response_message` helper accepts `turn_output_tokens` and `thinking_buffer_len` keyword arguments** (both default 0 for backward-compat with existing callers). The decision tree is now:
  1. Saw a dispatched tool call → tool-call variant (actionable: "ask for a direct answer")
  2. Tokens emitted but nothing in thinking buffer → **unclassified variant (new)** with token count
  3. Otherwise → classic "thinking exhausted the budget" variant

### Context
Found by investigating a Qwen3.5-122B screenshot where the user saw the classic "模型沒有產生任何回應 — 可能 thinking 用光輸出 token" message after a 24-output-token turn. The `-q` oneshot path returned a correct 282-token response for the same query, isolating the bug to the TUI layer (runtime layer is fine — wave2-1a P3 assembly handles thinking blocks correctly). The empty-response fallback at `app.py:L1665` pre-dates wave2 and had no observability — this PR adds the single missing log line so the next occurrence is fully diagnosable.

### Tests
- **`tests/test_tui/test_empty_response_i18n.py`** — 6 new tests:
  - Unclassified variant in English includes the token count and references `max_tokens`/`budget`
  - Unclassified variant in Chinese includes the token count and references `max_tokens`/`thinking_budget`
  - Classic "thinking exhausted" variant fires when thinking buffer has content (even with positive tokens)
  - Classic variant fires when both tokens and thinking are zero (pre-wave2 default)
  - Tool-call variant still wins precedence over unclassified when both conditions could apply
  - Legacy callers without the new kwargs still get the classic message (backward-compat)
- Existing 26 `test_empty_response_i18n.py` tests still pass — **32 total**, no regressions.
- Full `tests/test_tui/` sweep: **372 passed**.

## Unreleased — Wave2-1a P5: conversation_db thinking persistence + FTS5 (wave2-1a COMPLETE)

### Added
- **`messages` table gains `content_type` + `signature` columns.** Idempotent schema: fresh DBs get both via the new `CREATE TABLE`; pre-P5 DBs get them via `ALTER TABLE ADD COLUMN` gated on `PRAGMA table_info` so re-runs are no-ops. Legacy rows with NULL content_type are still matched by the text-only search filter via `COALESCE(m.content_type, 'text')`.
- **`ConversationDB.log_message`** accepts `content_type` and `signature` kwargs defaulted to pre-P5 values. `log_thinking(conv_id, content, signature, created_at)` convenience wrapper pins role=assistant, content_type=thinking.
- **`ConversationDB.search(query, content_type=None)`** optional filter: "text" only, "thinking" only, or both. `SearchResult.content_type` field exposed so UI can render thinking matches differently.
- **`ConversationRuntime._db_log_thinking(content, signature)`** called from the assembly path so every assistant turn that produced reasoning lands in FTS5 alongside the visible text log.

### Migration notes
- Pre-P5 DB files auto-upgrade on first open. PRAGMA-gated, idempotent, logs INFO per column added.
- Signature bytes round-trip byte-for-byte through SQLite.
- Rows written before P5 (NULL content_type) still searchable — COALESCE maps them to 'text'.

### Tests
- **`tests/test_runtime/test_conversation_db_thinking_wave2_1a_p5.py`** — 11 new tests: 3 migration (fresh / legacy / idempotent), 1 log_message back-compat, 2 log_thinking (role+type+content, signature byte-opacity), 5 search (no-filter, thinking-only, text-only, SearchResult.content_type field, legacy NULL → text)
- Full sweep: **1709 passed**, no regressions (1698 P4 + 11 new)

### Wave2-1a spec status: COMPLETE ✅

| Phase | PR | Scope | Tests | Sweep |
|---|---|---|---|---|
| P1 | #26 | `ThinkingBlock` dataclass + order validator | 16 | 1658 |
| P2 | #27 | `openai_compat` parses 5 provider shapes | 19 | 1677 |
| P3 | #28 | Assembly + Session serialization + isinstance sweep | 10 | 1687 |
| P4 | #29 | Compressor atomicity + outbound drop warning | 11 | 1698 |
| **P5** | **this** | `conversation_db` migration + FTS5 thinking search | **11** | **1709** |

**Total delta: 67 new tests, +51 test sweep (1658 → 1709), 5 merge-ready stacked PRs.**

Thinking is now a first-class ContentBlock end-to-end: parsed from 5 provider shapes (P2), stored in `Message.content` (P3), serialized to session JSON (P3), counted by `estimated_tokens()` (P3), preserved as atomic pair with adjacent tool_use during compression (P4), dropped-with-warn on outbound for OpenAI-compat (P4), indexed in `conversation_db` FTS5 with content_type filter (P5). A future native `AnthropicProvider` now has a clean path to plug in extended-thinking + tool-use multi-turn without touching the data model.

## Unreleased — Wave2-1a P4: Compressor atomicity + explicit outbound drop

### Added
- **`ContextCompressor._micro_compact` treats `(Thinking*, ToolUse)` as an atomic pair.** When a stale `ToolUseBlock` is dropped (because a later call to the same file made it redundant), any `ThinkingBlock` that immediately precedes it in the same assistant message is also dropped. The while-loop handles the Anthropic pattern where a long reasoning trace is split across multiple consecutive thinking blocks before a single tool_use. Without this, signed thinking would be orphaned — a future Anthropic-direct provider's signature verification would fail on the next request round-trip because thinking-without-its-paired-tool_use is invalid in the extended-thinking state machine. Unsigned thinking (Qwen, DeepSeek) is harmless to drop, but the pairing rule keeps the P1 ordering invariant trivially valid across compressions.
- **Thinking-only leftover messages are dropped.** If pruning a message's sole tool_use + its preceding thinking leaves the message with nothing but more thinking blocks (no text, no other tool uses), the whole message is dropped — orphaned reasoning with no load-bearing connection to subsequent turns has no value. Messages with non-thinking siblings are preserved.
- **`openai_compat._convert_message` explicitly counts and warns on dropped thinking.** Previously the drop was implicit (the has_multiple branch only handled TextBlock + ImageBlock and thinking fell through the unhandled gap). Now the branch has a named `elif isinstance(block, ThinkingBlock)` arm that increments a counter, and `_warn_thinking_dropped_once(count)` fires a warning exactly once per process the first time any request sends a reasoning-model assistant message through the outbound serializer. This is observability, not a behavior change — the drop itself is still the correct behavior for OpenAI-compat servers which reject unknown content types.

### Decisions recorded
- **Outbound default: strip, not round-trip.** The P4 plan floated a `strip_thinking_on_outbound` config flag defaulting to pass-through. We chose the opposite: strip by default, because:
  1. The only current provider is `OpenAICompatProvider` and every known OpenAI-compat server (vLLM, DeepSeek, OpenAI, Qwen) would 400 on unknown content types.
  2. A native `AnthropicProvider` will override `_convert_message` to emit structured thinking; the round-trip path lives in that override, not in a flag on the base class.
  3. YAGNI: no real proxy wants round-tripped thinking today. Adding the flag would be a configuration surface with no consumer.
- **Atomic pair window: immediately preceding only.** The fix pops ThinkingBlocks via a while-loop that only walks backward from the dropped ToolUseBlock within the same message. We do not attempt to preserve thinking that was emitted in a different message — Anthropic's extended-thinking state machine ties thinking to the tool_use in the same assistant message, not across turns.

### Tests
- **`tests/test_runtime/test_compressor_thinking_wave2_1a_p4.py`** — 6 new tests: single thinking+tool_use pair atomicity, multiple consecutive thinking blocks before a tool_use, kept tool_use preserves its thinking, thinking-only leftover message dropped, preceding TextBlock sibling preserved (only thinking gets popped), compressed session still satisfies the P1 order invariant.
- **`tests/test_api/test_outbound_thinking_wave2_1a_p4.py`** — 5 new tests: warn-once on first drop, warn-once across 10 requests, warn count reflects multiple thinking blocks, no warning for pure-text messages, visible text content survives drop (observability-only guarantee).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1698 passed**, no regressions (1687 from P3 + 11 new).

### Context
P4 of the 5-phase thinking-blocks-first-class spec. Compressor atomicity was the main architectural risk in the spec — without it, signed thinking would silently break on future Anthropic-direct provider integration. Outbound explicit drop is the observability half: the drop is still the right behavior today, but now it's visible in the logs rather than a silent accident. P5 (conversation_db persistence + FTS5 thinking search) is next and final.

## Unreleased — Wave2-1a P3: ThinkingBlock assembly + session persistence

### Added
- **`conversation.py` assistant assembly prepends thinking blocks.** The stream loop accumulates `thinking_parts` from `StreamThinkingDelta` events. At assembly time, a single merged `ThinkingBlock(content="".join(parts))` is prepended to `assistant_blocks` before any `TextBlock` / `ToolUseBlock`. The P1 `validate_assistant_content_order` is called defensively so a future refactor that reorders blocks fails loudly at the broken call site.
- **`Session` serializes thinking end-to-end.** `_block_to_dict` / `_dict_to_block` handle `{"type": "thinking", "thinking": "...", "signature": "..."}` (Anthropic-compatible shape; P5 reuses it). Pre-P5 rows missing the signature column rehydrate with `signature=""`.
- **`Session.estimated_tokens()` counts thinking.** DeepSeek-R1 sessions with 10K tokens of reasoning no longer look empty to the proactive compactor.

### Isinstance audit sweep
Grep-based sweep found 22 `isinstance(block, TextBlock|ToolUseBlock)` chains. Verified behavior:

- `session.py` serialization + estimated_tokens — **fixed here** (required).
- `compressor.py` (5 chains) — silently drops thinking. **Safe for OpenAI-compat**; P4 fixes the Anthropic round-trip case.
- `openai_compat.py` `_convert_message` (3 chains) — drops thinking from outbound parts list, solo-thinking falls through to empty content. **Does not crash**; correct for OpenAI-compat. P4 wires the Anthropic round-trip.
- `swarm/coordinator.py`, `runtime/vision.py`, `utils/search.py`, `cli/oneshot.py` (6 chains) — read-only text extractors for display / search / summary. Silently skipping thinking is correct behavior.

No chain raises on `ThinkingBlock`.

### Tests
- **`tests/test_runtime/test_thinking_assembly_wave2_1a_p3.py`** — 10 new tests: session serialization round-trip (5 inc. byte-opaque signature, P5-forward missing-column tolerance, full Session.to_dict/from_dict), estimated_tokens with + without thinking (2), outbound `_convert_message` with `(Thinking, Text)` and solo thinking (2), order validator defensive call (1).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1687 passed**, no regressions.

### Context
P3 of the 5-phase thinking-blocks-first-class spec. After P3, thinking content lands in `Session.messages` for the first time — previously it was stream-only and discarded on block_stop. P4 (outbound round-trip + compressor atomicity) and P5 (conversation_db + FTS5) follow.

## Unreleased — Wave2-1a P2: ThinkingBlock inbound parsing

### Added
- **`MessageResponse.thinking: tuple[ThinkingBlock, ...] = ()`** side-channel field for provider-reported thinking blocks. Non-thinking providers leave it empty. P3 is where these move into the assembled assistant `Message.content`; P2 only surfaces them on the response object so downstream assembly can see them.
- **`llm_code/api/openai_compat.py` now extracts reasoning content** from 5 provider shapes:
  - `message.reasoning_content` (DeepSeek-R1 / DeepSeek-reasoner / Qwen QwQ / vLLM) — scalar string
  - `message.reasoning` (OpenAI o-series newer SDK) — scalar string
  - Anthropic-style structured blocks: `message.content` is a list containing `{"type": "thinking", "thinking": "...", "signature": "..."}` — signature preserved byte-for-byte (never normalized, decoded, or trimmed — Anthropic verifies it server-side on the next request echo)
  - Streaming `delta.reasoning_content` — emits `StreamThinkingDelta` chunks so the TUI's existing flush logic picks them up
  - Streaming `delta.reasoning` — same, for o-series
- **`_extract_reasoning_text(source)` and `_extract_anthropic_thinking(content)`** helpers in openai_compat provide the extraction logic. Both are defensive: non-string fields, non-list inputs, and malformed list entries are silently skipped rather than crashing the parser.
- **Non-streaming parse** now handles both scalar `message.content` (unchanged) and Anthropic-style structured content list — text blocks become `TextBlock`, thinking blocks go to the side channel.
- **Streaming parse** now handles interleaved thinking + text in a single chunk: thinking is emitted first so the TUI flushes it before the visible text arrives (stable ordering pinned by test).

### Context
This is P2 of the thinking-blocks-first-class spec (see `docs/superpowers/specs/2026-04-09-llm-code-thinking-blocks-first-class-design.md`, local-only). P1 added the data model; P2 makes the provider parser actually populate it. Nothing downstream consumes `MessageResponse.thinking` yet — P3 is where `conversation.py` accumulates these and prepends them into `Message.content` before assembly.

Empty-string reasoning chunks are ignored so turns without thinking don't emit zero-length blocks. This matters for cost tracking and compression: a no-op thinking chunk would otherwise inflate token estimates in P3.

### Tests
- **`tests/test_api/test_openai_compat_thinking.py`** — 19 new tests:
  - 5 unit tests for `_extract_reasoning_text`: field priority, fallback, non-string rejection, empty-string treated as absent
  - 5 unit tests for `_extract_anthropic_thinking`: structured list walking, byte-opaque signature, signature default, scalar/None input rejection, malformed-entry skipping
  - 5 integration tests for non-streaming `_parse_response`: DeepSeek reasoning_content, OpenAI o-series reasoning, Anthropic structured, no-reasoning default, reasoning + tool_call combo
  - 4 streaming integration tests: reasoning_content chunks → StreamThinkingDelta, OpenAI reasoning field, empty chunks skipped, interleaved thinking+text ordering
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1677 passed**, no regressions.

## Unreleased — Wave2-1a P1: ThinkingBlock as first-class ContentBlock

### Added
- **`llm_code/api/types.py` `ThinkingBlock`** — new frozen dataclass with `content: str` and `signature: str = ""`. Represents the model's reasoning / chain-of-thought content as a structured block instead of a stream-only event. The `signature` field is provider-opaque (Anthropic signs thinking blocks for verbatim round-trip; Qwen / DeepSeek / OpenAI o-series leave it empty).
- **`ContentBlock` Union** now includes `ThinkingBlock` as the first member. Widening is additive: every existing `isinstance(block, ContentBlock)` check continues to work; any downstream consumer that doesn't yet know about thinking blocks simply won't match its branch (audit sweep for those is P3).
- **`llm_code/api/content_order.py`** — pure `validate_assistant_content_order(blocks)` that raises `ThinkingOrderError` (with `.index`, `.offending_type`, `.preceding_type`) when any thinking block appears after a non-thinking block. Empty tuples and tuples without any thinking blocks pass trivially, so the entire existing codebase stays valid — P1 lands with zero runtime effect.

### Context
The original wave2-1a plan was a small "thinking block order validator" sub-PR. The audit verification pass discovered the real architectural gap: llm-code has no `ThinkingBlock` ContentBlock type at all. `openai_compat.py` has zero references to `reasoning_content`; DeepSeek-R1 / OpenAI o-series / Qwen QwQ thinking is silently discarded at the API parsing layer. The current "working" state only holds because of a single provider × single thinking-mode coincidence (OpenAI-compat + Qwen3 `<think>` tag mode). Any attempt to add a native AnthropicProvider with extended thinking + tool use would break immediately on multi-turn, because Anthropic requires signed thinking blocks to be echoed back in subsequent requests.

This PR is **P1 of a 5-phase spec** (`docs/superpowers/specs/2026-04-09-llm-code-thinking-blocks-first-class-design.md`). P1 introduces the data model only — no producer, no consumer, no persistence. P2 adds the inbound parser; P3 assembles thinking into `Message.content`; P4 handles outbound serialization + compressor atomicity; P5 adds DB persistence.

### Tests
- **`tests/test_api/test_thinking_block.py`** — 16 new tests: frozen dataclass + signature default + signature byte-opaque preservation, `ContentBlock` Union membership (thinking first, all existing members intact), validator happy paths (empty, single, multiple consecutive thinking, thinking before text, thinking before tool_use, no-thinking-at-all), validator violations (text before thinking, tool_use before thinking, interleaved thinking mid-sequence), error message includes index + neighboring types.
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1658 passed**, no regressions.

## Unreleased — Wave2-1d: CancelledError cleanup on interrupted tool (wave2-1 COMPLETE)

### Added
- **`tool_cancelled` hook event**, `{tool_name, tool_id}` payload, registered under `tool.*` glob group.
- **`_execute_tool_with_streaming`** wraps progress-queue + future-await in `try/except asyncio.CancelledError`. On cancel: fires `tool_cancelled` hook, yields `is_error=True` `ToolResultBlock`, re-raises. Yield-then-raise order is load-bearing — otherwise the session has an orphan `ToolUseBlock` with no matching `ToolResultBlock` and the next turn's payload is malformed.

### Fixed
- Interrupted tool calls (user ctrl+c, parent-task timeout) used to propagate `CancelledError` without any cleanup — conversation round-trip invariant broke silently. The ThreadPoolExecutor worker thread still runs to completion in the background (CPython constraint), but the session state is now consistent.

### Tests
- **`tests/test_runtime/test_wave2_1d_cancel_cleanup.py`** — 7 new tests: 3 hook registration (name / glob / exact), 3 cancellation contract (yield-before-reraise order, tool name in error content, payload schema), 1 source-level guard on the production try/except + yield/raise order + `tool_cancelled` fire.
- Full sweep: **1649 passed**, no regressions.

### Wave2-1 session recovery: COMPLETE ✅

| Sub | Status | PR |
|---|---|---|
| 1a thinking blocks P1–P5 | ✅ | #26–#30 |
| 1b Retry-After + ProviderTimeoutError | ✅ | #31 |
| 1c Empty counter + context pre-warn | ✅ | #32 |
| **1d CancelledError cleanup** | **✅** | **this** |

All 8 failure modes from the wave2 audit are now covered:

| Mode | Pre-wave2 | Post-wave2 |
|---|---|---|
| ToolNotFound | ✅ | ✅ |
| MalformedToolInput | ✅ | ✅ |
| ThinkingBlockOrder | ❌ | ✅ (wave2-1a P1–P5, reframed as architecture) |
| RateLimited | ⚠️ | ✅ (wave2-1b Retry-After) |
| ProviderTimeout | ⚠️ | ✅ (wave2-1b ProviderTimeoutError) |
| ContextWindowExceeded | ⚠️ | ✅ (wave2-1c pre-warning) |
| EmptyAssistantResponse | ⚠️ | ✅ (wave2-1c counter) |
| **InterruptedToolCall** | **⚠️** | **✅ (wave2-1d)** |

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

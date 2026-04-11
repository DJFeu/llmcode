# E2E TUI Pilot Tests

**185 scenarios · 13 files · ~55 seconds.**

These tests boot a real `LLMCodeTUI` inside Textual's `App.run_test()`
pilot and drive it with actual keystrokes. Unlike the unit tests in
`tests/test_tui/` (which mock widgets or import individual classes),
the pilot layer mounts the full widget tree, routes events through
the real dispatcher, and asserts on runtime state — the goal is to
catch bugs that pytest-green unit tests miss: autocomplete dropdowns,
modal scroll, focus chains, keybinding dispatch, status-bar updates,
voice flow end-to-end.

## How the fixtures work

`conftest.py` provides two async fixtures:

- `pilot_app` — boots `LLMCodeTUI` with `_init_runtime` and `_init_mcp`
  stubbed to no-ops, so no tool registry / MCP servers / LLM provider /
  session manager are created. The TUI layer runs for real; everything
  below it is a blank slate.
- `pilot_voice_app` — same plus a `VoiceConfig` with `backend="local"`
  and VAD tuned for deterministic tests.

Every scenario that needs runtime state (session, cost tracker,
checkpoint manager, task manager, etc.) constructs a MagicMock or
SimpleNamespace stand-in and attaches it to `app._<field>` inside the
test. This keeps each scenario hermetic and independent.

## Slash command coverage matrix

`COMMAND_REGISTRY` has 52 `CommandDef` entries (plus `/quit` as an
alias for `/exit`). This matrix tracks the E2E depth for each one:

| Legend | Meaning |
|---|---|
| **DEEP** | Dedicated scenario(s) in this directory that exercise the real handler path and assert on observable state change (chat entry, runtime field, file system, etc.) |
| **SMOKE** | Exercised by `test_all_slash_commands.py::test_slash_command_dispatches_without_crash` — asserts the dispatcher can route the command without raising, but no behavioral assertion |
| **MODAL** | Dedicated scenario pushes the modal and asserts on widget state |
| **SKIP** | Intentionally not E2E-tested here; behavior is covered by other layers (unit tests, meta tests, or is a lifecycle-affecting command like `/exit`) |

### Core UX (13 commands)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/help` | **MODAL** | `test_help_modal.py` | 52-item scroll, tab switch, Esc, End/Home |
| `/clear` | **DEEP** | `test_basic_toggles.py` | Removes all chat children |
| `/copy` | **DEEP** | `test_basic_toggles.py` | Happy path + empty-chat fallback |
| `/cancel` | **DEEP** | `test_basic_toggles.py` | With and without runtime |
| `/yolo` | **DEEP** | `test_basic_toggles.py` | Permission mode flip |
| `/thinking` | **DEEP** | `test_basic_toggles.py` | All 4 sub-modes |
| `/vim` | **DEEP** | `test_basic_toggles.py` | Toggle InputBar + StatusBar reactive |
| `/exit` | **SKIP** | — | Quits the app loop |
| `/quit` | **SKIP** | — | Alias for `/exit` |
| `/model` | **DEEP** | `test_info_commands.py` | Bare show + `/model route` |
| `/theme` | **DEEP** | `test_theme_switch.py` | Bare list, dracula switch, unknown name |
| `/settings` | **SKIP** | — | Pushes a modal; Static is empty until render |
| `/update` | **DEEP** | `test_heavy_commands.py` | Worker dispatched with name="update" |

### Voice (1 command, 5 scenarios)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/voice` | **DEEP** | `test_voice_flow.py` | on/off/typo/VAD/mic-permission hint/Ctrl+G hotkey toggle |

### Input / interaction

| Feature | Depth | File | Notes |
|---|---|---|---|
| Slash dropdown | **DEEP** | `test_slash_dropdown.py` | Open/filter/nav/accept(Tab/Enter/→)/cancel |
| Prompt history | **DEEP** | `test_prompt_history_e2e.py` | ↑↓ recall, draft restore, dropdown/vim/multiline suppression, typing reset |
| Multiline | **DEEP** | `test_multiline_input.py` | Shift+Enter, Ctrl+J, history suppress |
| `/image` | **DEEP** | `test_image_flow.py` | PNG load, no-arg, missing file, accumulate |
| Cycle agent | **DEEP** | `test_cycle_agent.py` | build/plan/suggest, Shift+Tab, Ctrl+Y |
| Boot banner | **DEEP** | `test_boot_banner.py` | Voice hint visibility |

### Info / config (11 commands)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/cost` | **DEEP** | `test_info_commands.py` | Tracker fallback + format_cost path |
| `/gain` | **DEEP** | `test_info_commands.py` | Days arg + default 30 |
| `/profile` | **DEEP** | `test_info_commands.py` | With and without profiler |
| `/cache` | **DEEP** | `test_info_commands.py` | list / clear / probe / usage |
| `/personas` | **DEEP** | `test_info_commands.py` | Lists BUILTIN_PERSONAS |
| `/budget` | **DEEP** | `test_info_commands.py` | Valid int + ValueError path |
| `/set` | **DEEP** | `test_info_commands.py` | Temperature update, unknown key, no-args |
| `/config` | **DEEP** | `test_info_commands.py` | Lists core fields |
| `/cd` | **DEEP** | `test_info_commands.py` | Bare, valid, missing |
| `/map` | **DEEP** | `test_info_commands.py` | Empty dir + small repo |
| `/dump` | **DEEP** | `test_info_commands.py` | Writes `.llmcode/dump.txt` |

### Session / memory / history (5 commands)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/session` | **DEEP** | `test_session_memory.py` | Stub usage pointer |
| `/memory` | **DEEP** | `test_session_memory.py` | set/get/delete/bare/empty/no-store |
| `/undo` | **DEEP** | `test_session_memory.py` | With/without mgr, list, single-step, multi-step |
| `/diff` | **DEEP** | `test_session_memory.py` | No checkpoints, with diff, clean |
| `/compact` | **DEEP** | `test_session_memory.py` | No runtime, with runtime, default keep |
| `/checkpoint` | **DEEP** | `test_checkpoint_flow.py` | save / list / resume + cost_tracker round-trip |
| `/export` | **DEEP** | `test_export_flow.py` | Path / default filename / empty session |

### Workflow / coordination (10 commands)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/plan` | **DEEP** | `test_workflow_commands.py` | Toggle + status update |
| `/mode` | **DEEP** | `test_workflow_commands.py` | Bare, plan, suggest, unknown |
| `/harness` | **DEEP** | `test_workflow_commands.py` | No runtime fallback |
| `/search` | **DEEP** | `test_workflow_commands.py` | Empty, FTS5 results, no matches |
| `/cron` | **DEEP** | `test_workflow_commands.py` | List empty/populated, delete hit/miss |
| `/task` | **DEEP** | `test_workflow_commands.py` | list empty/populated, no mgr, new |
| `/swarm` | **DEEP** | `test_workflow_commands.py` | No mgr, active, coordinate usage |
| `/orchestrate` | **DEEP** | `test_workflow_commands.py` | No args, no runtime, worker dispatched |
| `/hida` | **DEEP** | `test_workflow_commands.py` | No runtime, no profile, with profile |
| `/personas` | (above) | `test_info_commands.py` | |

### 外掛生態系 (3 commands — plugin / skill / mcp)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/plugin` | **DEEP** | `test_plugin_skill_mcp.py` | Invalid repo, install (clone+enable+reload+load_tools), enable, disable, unsafe-name guard |
| `/skill` | **DEEP** | `test_plugin_skill_mcp.py` | Install, enable, disable, remove (delete dir), unsafe-name, marketplace push |
| `/mcp` | **DEEP** | `test_plugin_skill_mcp.py` | install (config write + hot-start), remove, remove-missing, marketplace push |

### Heavy / IO-bound (8 commands)

| Command | Depth | File | Notes |
|---|---|---|---|
| `/init` | **DEEP** | `test_heavy_commands.py` | Worker dispatched + template-missing error |
| `/index` | **DEEP** | `test_heavy_commands.py` | Bare no-index, bare with-index, rebuild |
| `/knowledge` | **DEEP** | `test_heavy_commands.py` | Empty, populated, compiler-unavailable |
| `/analyze` | **DEEP** | `test_heavy_commands.py` | Runs + stores context; failure path |
| `/diff_check` | **SMOKE** | — | Covered by `test_all_slash_commands.py` |
| `/lsp` | **DEEP** | `test_heavy_commands.py` | Not-started fallback |
| `/vcr` | **DEEP** | `test_heavy_commands.py` | Status, start, stop, list-empty, already-active |
| `/ide` | **DEEP** | `test_heavy_commands.py` | Disabled, connect guidance, connected status |

## Meta / cross-cutting coverage

| Test | File | What it enforces |
|---|---|---|
| `test_dispatcher_has_all_52_commands` | `tests/test_tui/test_command_dispatcher.py` | Every `CommandDef` has a matching `_cmd_*` method |
| `test_registry_has_no_dead_handlers` | `tests/test_tui/test_command_dispatcher.py` | Every `_cmd_*` has a registry entry — prevents missing autocomplete hints |
| `test_every_registry_command_has_handler` | `test_all_slash_commands.py` | E2E-layer duplicate of the same invariant |
| `test_unknown_command_does_not_crash_dispatcher` | `test_all_slash_commands.py` | `dispatch()` returns False instead of raising on unknown names |
| `test_slash_command_dispatches_without_crash` | `test_all_slash_commands.py` | Parameterized smoke over 12 remaining commands |

## When to add a new scenario

| Change type | Add what |
|---|---|
| New slash command | A `test_<area>.py::test_<cmd>_*` set of scenarios covering the happy path + every obvious error/missing-state branch |
| Fixing a runtime bug that pytest didn't catch | A regression scenario that would have failed against the pre-fix code |
| New keybinding | An entry in `test_boot_banner.py` if it shows in the banner hints, plus a `pilot.press(<key>)` scenario in the most relevant area file |
| New reactive on a widget | `rendered = _rendered_text(widget)` check in the scenario that triggers the state change |

## Runtime

- **13 files** under `tests/test_e2e_tui/`
- **185 scenarios** total
- **~55 seconds** on a 2026 MacBook Pro (M-series)
- **0 external dependencies** — no network, no mic, no LLM credentials, no real git repo
- **Deterministic** — no time.sleep, no retries, no flaky assertions

Run just this suite:

```bash
.venv/bin/pytest tests/test_e2e_tui/ -q
```

Run everything:

```bash
.venv/bin/pytest -q
```

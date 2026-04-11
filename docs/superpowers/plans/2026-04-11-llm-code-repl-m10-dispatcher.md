# M10 — Dispatcher Relocation + 62 → 58 Commands

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Move the 62-command `CommandDispatcher` from `tui/command_dispatcher.py` to `view/dispatcher.py`, strip all direct widget references in favor of `self._view: ViewBackend` calls, drop 4 legacy commands, and transliterate the dispatcher test suite (~150 tests) to `tests/test_view/test_dispatcher.py`. After M10, the dispatcher is completely view-agnostic and is the serializing point where M11 can flip from the old entry point to the new one.

**Architecture:** The dispatcher's 62 `_cmd_*` methods currently do two kinds of things: (a) logic operations against `self._runtime` (unchanged), (b) widget access like `self._app.query_one(ChatScrollView).mount(widget)` (must be rewritten). The rewrite replaces (b) with ViewBackend Protocol calls: `self._view.render_message`, `self._view.print_info`, `self._view.start_streaming_message`, etc. Any command that can't be expressed through the Protocol either needs a new Protocol method (edit base.py in this milestone) or gets dropped.

**Tech Stack:** Python refactoring, the M1 `ViewBackend` ABC, pytest.

**Spec reference:** §4.1 package layout, §7.2 removed commands, §9.2 test transliteration pattern.

**Dependencies:** M1–M9 complete. This is the final foundation milestone before M11 cutover. All ViewBackend implementations must be stable.

---

## File Structure

- Create: `llm_code/view/dispatcher.py` — relocated + rewritten dispatcher (~1500 lines, ~90% line-identical to old with widget calls swapped)
- Modify: `llm_code/view/base.py` — add 1-2 Protocol methods discovered during rewrite (if needed)
- Modify: `llm_code/view/repl/backend.py` — implement any new Protocol methods added to base.py
- **Keep in place** (do NOT delete yet): `llm_code/tui/command_dispatcher.py` — the old file continues to exist until M11 so the current main-branch TUI keeps working during development. M11 is where tui/ gets deleted entirely.
- Create: `tests/test_view/test_dispatcher.py` — ~150 transliterated tests, ~2000 lines
- Delete at end of M10: none (deletions happen in M11)

---

## Tasks

### Task 10.1: Dispatcher surface inventory

**Files:** none (analysis only, but the findings drive 10.2+)

- [ ] **Step 1: Read `llm_code/tui/command_dispatcher.py` completely.**

Expected: ~1500 lines, 62 `_cmd_*` methods, a `COMMAND_REGISTRY` reference, and many widget-accessing calls.

- [ ] **Step 2: Build a widget-access inventory.**

Run:
```bash
grep -n 'query_one\|self\._app\.\|ChatScrollView\|InputBar\|HeaderBar\|StatusBar\|mount\|remove\|refresh\|UserMessage\|AssistantText\|chat_view' llm_code/tui/command_dispatcher.py > /tmp/m10-widget-access.txt
wc -l /tmp/m10-widget-access.txt
```

Expected: 100-200 lines of widget access matches. These are the callsites the rewrite must replace.

- [ ] **Step 3: Group widget access by pattern.**

Typical patterns found in v1.23.x dispatcher:
- `self._app.query_one(ChatScrollView).mount(UserMessage(text))` → `self._view.render_message(MessageEvent(role=Role.USER, content=text))`
- `self._app.query_one(ChatScrollView).mount(AssistantText(text))` → `self._view.render_message(MessageEvent(role=Role.ASSISTANT, content=text))`
- `self._app.query_one(InputBar).value = text` → no direct equivalent; drop (InputBar mutation by dispatcher is rare and usually a UX bug)
- `self._app.query_one(StatusBar).model = name` → `self._view.update_status(StatusUpdate(model=name))`
- `self._app.query_one(HeaderBar).branch = b` → `self._view.update_status(StatusUpdate(branch=b))`
- `self._app.push_screen(MarketplaceScreen())` → `self._view.show_select(...)` with choices (§7.2 marketplace rewrite)
- `self._app.push_screen(QuickOpenScreen())` → `self._view.show_select(...)` + auto-preview (§7.2 quick_open rewrite)
- `self._app.exit()` → `self._view.coordinator.request_exit()` (or a new `view.request_exit()` Protocol method — decide in 10.2)
- Any custom Textual modal → `self._view.show_confirm/select/text/checklist` via dialog popover

- [ ] **Step 4: Produce a migration table.**

Write your findings to `/tmp/m10-migration-plan.md`:

```markdown
# M10 Dispatcher Rewrite — Migration Table

| Old call | Replacement | Notes |
|---|---|---|
| `query_one(ChatScrollView).mount(UserMessage(...))` | `self._view.render_message(MessageEvent(role=USER, content=..))` | |
| `query_one(ChatScrollView).mount(AssistantText(...))` | `self._view.render_message(...)` with Role.ASSISTANT | |
| `query_one(StatusBar).model = X` | `self._view.update_status(StatusUpdate(model=X))` | |
| `push_screen(MarketplaceScreen)` | `show_select` over plugin list + `print_panel` for details | §7.2 rewrite |
| `push_screen(QuickOpenScreen)` | `show_select` + auto-preview in scrollback | §7.2 rewrite |
| `self._app.exit()` | `self._view.request_exit()` NEW PROTOCOL METHOD | add in 10.2 |
| `mount(StreamingChunk)` during streaming | `handle.feed(chunk)` via `start_streaming_message` handle | |
| Modal Settings screen | `show_text_input` with editor fallback | |
| Any bespoke modal | `show_select` / `show_confirm` | |

(Fill in row for each distinct widget-access pattern found.)
```

This table is your working plan for 10.3.

### Task 10.2: Extend ViewBackend Protocol if needed

**Files:** Modify `llm_code/view/base.py`, `llm_code/view/repl/backend.py`

Based on the 10.1 inventory, you probably need to add these Protocol methods that aren't expressible as combinations of existing ones:

- `request_exit()` — graceful exit (REPL: coordinator.request_exit; Telegram: stop bot)
- Possibly `get_current_input_text()` + `set_input_text(text)` — for a few commands like `/vim` that need to mutate the current input

- [ ] **Step 1: Add `request_exit` abstract method to ViewBackend.**

```python
@abstractmethod
def request_exit(self) -> None:
    """Signal the backend to exit its run() loop at the next opportunity."""
```

- [ ] **Step 2: Implement `request_exit` in REPLBackend.**

```python
def request_exit(self) -> None:
    self._coordinator.request_exit()
    if self._coordinator._app is not None and self._coordinator._app.is_running:
        self._coordinator._app.exit()
```

- [ ] **Step 3: Implement `request_exit` in StubRecordingBackend** (tests/test_view/_stub_backend.py):

```python
def request_exit(self) -> None:
    self._running = False
```

- [ ] **Step 4: Update the Protocol conformance test** in `tests/test_view/test_protocol_conformance.py` — add `"request_exit"` to the `expected` abstract-method set in `test_view_backend_has_expected_abstract_methods`.

- [ ] **Step 5: Run conformance tests** — `pytest tests/test_view/test_protocol_conformance.py -v` → all pass.

- [ ] **Step 6: Commit** — `git commit -am "feat(view): add request_exit to ViewBackend Protocol"`

### Task 10.3: Write view/dispatcher.py by rewriting tui/command_dispatcher.py

**Files:** Create `llm_code/view/dispatcher.py`

This is the bulk task — 1500+ lines of rewriting. Execute it in 4 sub-tasks to keep each commit reviewable.

- [ ] **Step 1: Copy + import-only rewrite.**

```bash
cp llm_code/tui/command_dispatcher.py llm_code/view/dispatcher.py
```

Then in `llm_code/view/dispatcher.py`:

1. Rename the class if needed — probably keep `CommandDispatcher`.
2. Update imports:
   - Remove all `from llm_code.tui.*` widget imports (ChatScrollView, InputBar, HeaderBar, StatusBar, chat_widgets, etc.)
   - Add `from llm_code.view.base import ViewBackend`
   - Add `from llm_code.view.types import MessageEvent, Role, RiskLevel, StatusUpdate`
   - Add `from llm_code.view.dialog_types import Choice, DialogCancelled`
3. Change constructor: instead of taking `self._app: "LLMCodeTUI"`, take `self._view: ViewBackend` and `self._runtime: Runtime`.
4. Do NOT yet touch the `_cmd_*` method bodies — only the constructor signature and imports.

Run syntax check. Expected: fails because the `_cmd_*` methods still reference widgets. That's fine for this step.

Commit: `git add llm_code/view/dispatcher.py && git commit -m "refactor(view): relocate dispatcher — constructor + imports only (not yet functional)"`

- [ ] **Step 2: Rewrite the "simple" commands (20-ish).**

Target commands that don't push screens or touch widgets beyond render/status: `/version`, `/help`, `/clear`, `/exit`, `/mode`, `/branch`, `/cwd`, `/model`, `/cost`, `/token`, `/history`, `/retry`, `/copy`, `/think`, `/limits`, `/verbose`, `/compact`.

For each: replace widget calls with `self._view.*` calls per the migration table.

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('llm_code/view/dispatcher.py').read()); print('OK')"` — should pass.

Commit: `git commit -am "refactor(view): rewrite 20 simple commands to use ViewBackend"`

- [ ] **Step 3: Rewrite the "modal" commands (15-ish).**

Target commands that push Textual screens: `/marketplace`, `/plugin`, `/skills`, `/mcp`, `/tools`, `/permission`, `/keybindings`, `/session`, `/checkpoint`, `/budget`, `/rate-limit`, `/telemetry`.

For each:
- If the command shows a "list + detail" view → rewrite as `show_select` + `print_panel`
- If the command shows a "fuzzy pick file" → rewrite as `show_select` + auto-preview
- If the command shows a "yes/no confirm" → `show_confirm`
- If the command shows a "text input" → `show_text_input`
- If the command shows a "multi-pick" → `show_checklist`

Commit: `git commit -am "refactor(view): rewrite 15 modal commands to dialog flow"`

- [ ] **Step 4: Rewrite the "streaming" commands (10-ish).**

Target commands that interact with live response streaming: `/stream`, `/run`, `/chat` (implicit non-slash turn), tool-related commands.

The dispatcher's main `run_turn` method (called by the input handler set in `backend.set_input_handler`) handles the streaming → LLM → tool-calls → streaming commit flow. This is the core integration point from spec §5.3.

Commit: `git commit -am "refactor(view): rewrite streaming + tool flow using ViewBackend handles"`

- [ ] **Step 5: Drop the 4 legacy commands.**

Delete the method bodies for:
- `_cmd_scroll` (§7.2 replacement: terminal native)
- `_cmd_marketplace_browse` (folded into `_cmd_marketplace_list`)
- `_cmd_plugin_browse` (folded into `_cmd_plugin_list`)
- `_cmd_settings` (when called as a modal; retain a `_cmd_settings_edit` variant that opens `$EDITOR` on config.toml)

Remove the entries from `COMMAND_REGISTRY` in `cli/commands.py` as well.

Commit: `git commit -am "refactor(view): drop 4 legacy commands (/scroll /marketplace browse /plugin browse /settings modal)"`

- [ ] **Step 6: Add a drift guard test.**

```python
# tests/test_view/test_command_count.py
def test_command_count_is_58():
    from llm_code.cli.commands import COMMAND_REGISTRY
    names = [c.name for c in COMMAND_REGISTRY]
    assert len(names) == 58, f"Expected 58 commands, got {len(names)}: {names}"

def test_dropped_commands_not_present():
    from llm_code.cli.commands import COMMAND_REGISTRY
    names = {c.name for c in COMMAND_REGISTRY}
    for dropped in ["scroll", "marketplace-browse", "plugin-browse", "settings-modal"]:
        assert dropped not in names
```

Commit: `git commit -am "test(view): guard that M10 cut 62 -> 58 commands"`

### Task 10.4: Transliterate tests/test_tui/test_command_dispatcher.py

**Files:** Create `tests/test_view/test_dispatcher.py`

- [ ] **Step 1: Run the transliteration pattern on each test**

Follow §9.2 mechanical rewrite table. For each test in the old file:

1. Copy test body to new file
2. Replace `pilot_app` fixture with `stub_repl_pilot` (dispatcher tests don't need real terminal)
3. Replace `app.query_one(...)` assertions with pilot state queries (`pilot.info_lines`, `pilot.rendered_messages`, `pilot.status_updates`, etc.)
4. Replace `await pilot.press("...")` with `await pilot.submit("/command args")` where possible
5. Replace Textual modal interactions with scripted dialog responses (`pilot.script_confirms(...)`, `pilot.script_selects(...)`)

Representative transliterated test:

```python
@pytest.mark.asyncio
async def test_version_command_prints_version(stub_repl_pilot):
    from llm_code.view.dispatcher import CommandDispatcher
    dispatcher = CommandDispatcher(view=stub_repl_pilot.backend, runtime=None)
    stub_repl_pilot.set_dispatcher(dispatcher.run_turn)
    await stub_repl_pilot.submit("/version")
    assert any("llmcode" in line.lower() for line in stub_repl_pilot.info_lines)

@pytest.mark.asyncio
async def test_marketplace_list_shows_select(stub_repl_pilot, mock_runtime_with_plugins):
    from llm_code.view.dispatcher import CommandDispatcher
    dispatcher = CommandDispatcher(view=stub_repl_pilot.backend, runtime=mock_runtime_with_plugins)
    stub_repl_pilot.set_dispatcher(dispatcher.run_turn)
    # Script the user's choice in the select dialog
    stub_repl_pilot.script_selects("example-plugin")
    await stub_repl_pilot.submit("/marketplace")
    assert any(call[0] == "select" for call in stub_repl_pilot.dialog_calls)
    # After selection, the dispatcher should print the plugin panel
    assert len(stub_repl_pilot.panels) > 0
```

Target: ~150 transliterated tests. Some will need minor rewrites because the behavior changed (e.g. marketplace used to show a screen with plugin cards; now it shows a select).

- [ ] **Step 2: Run dispatcher tests** — `pytest tests/test_view/test_dispatcher.py -v` → ~150 pass (some may need iteration to get right).
- [ ] **Step 3: Commit** — `git commit -am "test(view): transliterate ~150 dispatcher tests to use stub_repl_pilot"`

### Task 10.5: Integration verification

**Files:** none (verification)

- [ ] **Step 1: Run all view tests** — `pytest tests/test_view/ -q` → 0 failures, expect ~450 tests total (protocol + pilot + coordinator + input_area + slash_popover + status_line + live_response + tool_events + dialog_popover + voice_overlay + dispatcher + command_count).

- [ ] **Step 2: Run all old TUI tests** — `pytest tests/test_tui/ tests/test_e2e_tui/ -q --tb=no` → must still pass (we haven't deleted `tui/` yet, so the old code is still alive).

- [ ] **Step 3: Push branch** — `git push origin feat/repl-mode`

---

## Milestone completion criteria

- ✅ `llm_code/view/dispatcher.py` exists and imports `ViewBackend`
- ✅ Constructor takes `view: ViewBackend` + `runtime: Runtime`
- ✅ No references to `tui.*` widget classes anywhere in `view/dispatcher.py`
- ✅ 58 commands registered (62 minus the 4 legacy drops)
- ✅ `request_exit` Protocol method added and implemented
- ✅ ~150 dispatcher tests transliterated and green
- ✅ Old `tui/command_dispatcher.py` still exists and still works (deletion is M11)
- ✅ All existing tests still green

## Estimated effort: ~8–12 hours

M10 is the largest milestone by code volume and is the highest risk for transliteration overrun (R4 from spec §10.1). If it takes longer than estimated, the fallback is F3: keep `tui/command_dispatcher.py` in place and only rewrite the widget calls in situ, leaving the file's location untouched until v2.1. This costs 30% more cleanup work later but unblocks the v2.0.0 release.

## Next milestone: M11 — Cutover (`m11-cutover.md`)

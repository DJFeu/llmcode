# M11 — Cutover: Entry Point Swap + tui/ Deletion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Rename `cli/tui_main.py` to `cli/main.py`, wire it to instantiate `REPLBackend` + `CommandDispatcher` via the new ViewBackend Protocol, then delete the entire `llm_code/tui/` package (~5000 lines) and `tests/test_tui/` + `tests/test_e2e_tui/` (~657 tests). This is the "flag day" milestone — after it, `tui/` is gone forever and the only frontend is REPL.

**Architecture:** A single atomic commit that (a) rewrites `cli/main.py`, (b) deletes `llm_code/tui/`, (c) deletes the old test tree, (d) updates `pyproject.toml` to remove `textual` dependency. Any tests or imports that still reference `tui.*` must be found and fixed in the same commit or the build breaks.

**Tech Stack:** Python refactoring, grep/find for stragglers, pytest for verification.

**Spec reference:** §4.1 deleted files, §8.2 M11 description, §12.1–12.2 file inventory.

**Dependencies:** M1–M10 complete. All ViewBackend test coverage green. The new dispatcher handles all 58 commands. Nothing in `llm_code/` production code imports from `tui/*` except `cli/tui_main.py`.

---

## File Structure

### Deletions (this milestone)

- `llm_code/tui/__init__.py`
- `llm_code/tui/app.py` (1358 lines)
- `llm_code/tui/chat_view.py`
- `llm_code/tui/chat_widgets.py`
- `llm_code/tui/command_dispatcher.py` (relocated version lives at view/dispatcher.py)
- `llm_code/tui/compaction_label.py`
- `llm_code/tui/diff_render.py`
- `llm_code/tui/header_bar.py`
- `llm_code/tui/input_bar.py`
- `llm_code/tui/marketplace.py`
- `llm_code/tui/mcp_approval.py`
- `llm_code/tui/quick_open.py`
- `llm_code/tui/runtime_init.py`
- `llm_code/tui/settings_modal.py`
- `llm_code/tui/spinner_verbs.py`
- `llm_code/tui/status_bar.py`
- `llm_code/tui/stream_parser.py`
- `llm_code/tui/streaming_handler.py` (463 lines)
- `llm_code/tui/theme.py`
- `llm_code/tui/themes.py`
- `llm_code/tui/tool_render.py`
- `llm_code/tui/dialogs/__init__.py`
- `llm_code/tui/dialogs/api.py` (relocated version lives at view/dialog_types.py)
- `llm_code/tui/dialogs/headless.py` (relocated as view/headless.py in this milestone)
- `llm_code/tui/dialogs/scripted.py` (relocated as view/scripted.py in this milestone)
- `llm_code/tui/dialogs/textual_backend.py`
- `llm_code/tui/ansi_strip.py` (relocated as view/repl/ansi_strip.py)
- `llm_code/tui/double_press.py` (relocated as view/repl/double_press.py)
- `llm_code/tui/prompt_history.py` (already relocated in M4 to view/repl/history.py)
- `llm_code/tui/keybindings.py` (already relocated in M4 to view/repl/keybindings.py)
- `tests/test_tui/` (~250 tests, ~9000 lines)
- `tests/test_e2e_tui/` (~200 tests, ~7000 lines)

### Renames

- `llm_code/cli/tui_main.py` → `llm_code/cli/main.py`

### Modifications

- `llm_code/cli/main.py` — rewrite main() body to use REPLBackend + CommandDispatcher
- `llm_code/cli/__init__.py` — update entry point export from `tui_main.main` to `main.main`
- `pyproject.toml` — remove `textual` dependency from the dependency list; update `[project.scripts]` entry if it points at `llmcode = llm_code.cli.tui_main:main`
- Any file with a stale `from llm_code.tui.*` import — find with grep, fix case-by-case

---

## Tasks

### Task 11.1: Relocate keepers

**Files:**
- Create: `llm_code/view/repl/ansi_strip.py` (copied from tui/ansi_strip.py)
- Create: `llm_code/view/repl/double_press.py` (copied from tui/double_press.py)
- Create: `llm_code/view/headless.py` (copied from tui/dialogs/headless.py)
- Create: `llm_code/view/scripted.py` (copied from tui/dialogs/scripted.py)

These files have no widget dependencies — they're pure utility modules that just happen to live under `tui/`. We move them to `view/` before deleting the rest of `tui/`.

- [ ] **Step 1: Copy the files.**

```bash
cp llm_code/tui/ansi_strip.py llm_code/view/repl/ansi_strip.py
cp llm_code/tui/double_press.py llm_code/view/repl/double_press.py
cp llm_code/tui/dialogs/headless.py llm_code/view/headless.py
cp llm_code/tui/dialogs/scripted.py llm_code/view/scripted.py
```

- [ ] **Step 2: Update imports within the copied files.**

Check each for `from llm_code.tui.*` imports — most will be `from llm_code.tui.dialogs.api import Choice`, which should become `from llm_code.view.dialog_types import Choice`.

- [ ] **Step 3: Update any consumers** — grep for imports of the new file locations to make sure they still work.

- [ ] **Step 4: Commit** — `git add llm_code/view/ && git commit -m "refactor(view): relocate ansi_strip, double_press, headless, scripted from tui/"`

### Task 11.2: Rewrite cli/main.py

**Files:**
- Delete: `llm_code/cli/tui_main.py`
- Create: `llm_code/cli/main.py`

- [ ] **Step 1: Write `llm_code/cli/main.py`.**

Use the old `tui_main.py` as a starting point — copy the entire file to `main.py`. Then rewrite the TUI-launching section (the part that creates `LLMCodeTUI` and calls `app.run(...)`):

```python
# New section: wire REPL backend + dispatcher + runtime

from llm_code.view.repl.backend import REPLBackend
from llm_code.view.dispatcher import CommandDispatcher
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.conversation import Runtime

def run_repl_mode(config: RuntimeConfig, cwd: Path, ...) -> None:
    """Launch the REPL backend with dispatcher and runtime wiring."""
    runtime = Runtime(config=config, cwd=cwd)
    backend = REPLBackend(config=config, runtime=runtime)
    dispatcher = CommandDispatcher(view=backend, runtime=runtime)
    backend.set_input_handler(dispatcher.run_turn)

    # Wire runtime → backend for status + session events
    runtime.on_status_change(backend.update_status)

    asyncio.run(backend.run())
```

Replace every call site that previously invoked `LLMCodeTUI(...).run(mouse=...)` with `run_repl_mode(...)`.

Delete `llm_code/cli/tui_main.py`:

```bash
git rm llm_code/cli/tui_main.py
```

- [ ] **Step 2: Update `pyproject.toml`.**

Find the `[project.scripts]` block. If it currently says:
```toml
[project.scripts]
llmcode = "llm_code.cli.tui_main:main"
```
Change to:
```toml
llmcode = "llm_code.cli.main:main"
```

- [ ] **Step 3: Verify the new entry point.**

```bash
/Users/adamhong/miniconda3/bin/python3 -c "from llm_code.cli.main import main; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit** — `git commit -am "refactor(cli): rename tui_main to main, wire REPLBackend"`

### Task 11.3: Delete llm_code/tui/ package

**Files:** Delete the entire `llm_code/tui/` directory except any files that are still being imported (which there should be zero of at this point).

- [ ] **Step 1: Inventory remaining tui/ consumers.**

```bash
grep -rn 'from llm_code\.tui\|import llm_code\.tui' llm_code/ tests/ scripts/ 2>/dev/null | grep -v __pycache__ | grep -v 'llm_code/tui/'
```

Expected: no output. If any matches remain, those must be fixed first (update the import to point at `llm_code.view.*` or `llm_code.cli.*`).

- [ ] **Step 2: Delete tui/.**

```bash
git rm -r llm_code/tui/
```

Expected: ~28 files removed, ~5000 lines deleted.

- [ ] **Step 3: Verify no imports break.**

```bash
/Users/adamhong/miniconda3/bin/python3 -c "import llm_code; import llm_code.cli.main; import llm_code.view.dispatcher; import llm_code.view.repl.backend; print('all imports OK')"
```

Expected: `all imports OK`.

- [ ] **Step 4: Commit** — `git commit -m "refactor: delete llm_code/tui/ package (5000+ lines; replaced by view/repl/)"`

### Task 11.4: Delete tests/test_tui/ and tests/test_e2e_tui/

**Files:** Delete `tests/test_tui/` and `tests/test_e2e_tui/`.

- [ ] **Step 1: Inventory.**

```bash
ls tests/test_tui/ tests/test_e2e_tui/ 2>/dev/null
```

Expected: ~15–20 files in each directory. These are the original 657 Textual tests — some transliterated into `tests/test_view/` during M4–M10, others irrelevant now.

- [ ] **Step 2: Delete.**

```bash
git rm -r tests/test_tui/ tests/test_e2e_tui/
```

- [ ] **Step 3: Verify pytest collection still works.**

```bash
/Users/adamhong/miniconda3/bin/python3 -m pytest tests/ --collect-only -q 2>&1 | tail -5
```

Expected: pytest collects ~5000 tests (the non-TUI tests) without errors.

- [ ] **Step 4: Commit** — `git commit -m "test: delete tests/test_tui/ and tests/test_e2e_tui/ (transliterated or obsolete)"`

### Task 11.5: Remove textual dependency from pyproject.toml

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Find the textual dependency.**

```bash
grep -n 'textual' pyproject.toml
```

Expected: one line, e.g. `"textual>=0.50",`.

- [ ] **Step 2: Remove it.**

Delete the line.

- [ ] **Step 3: Verify imports still work without textual installed.**

Optionally: `/Users/adamhong/miniconda3/bin/python3 -c "import textual; print(textual.__version__)"` should still work (textual is still installed in the venv), but `/Users/adamhong/miniconda3/bin/python3 -c "import llm_code; import llm_code.cli.main" ` must not error — i.e. our code no longer imports textual anywhere.

```bash
grep -rn '^import textual\|^from textual' llm_code/ tests/ 2>/dev/null
```

Expected: no output.

- [ ] **Step 4: Commit** — `git commit -am "chore(deps): remove textual dependency from pyproject.toml"`

### Task 11.6: Full green sweep

**Files:** none (verification)

- [ ] **Step 1: Run the entire test suite.**

```bash
/Users/adamhong/miniconda3/bin/python3 -m pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected:
- 0 failures
- ~5000 tests total (5354 v1.23.x minus ~657 deleted + ~750 new view tests ≈ 5447)
- Execution time < 10 minutes

- [ ] **Step 2: Smoke-run llmcode.**

```bash
/Users/adamhong/miniconda3/bin/python3 -m llm_code.cli.main --help 2>&1 | head -20
```

Expected: help text prints without errors.

Optionally start the REPL interactively for a quick eyeball:

```bash
/Users/adamhong/miniconda3/bin/python3 -m llm_code.cli.main
# Type /version, then /quit
```

Expected: REPL starts, `/version` prints the llmcode version, `/quit` exits cleanly.

- [ ] **Step 3: Push branch.**

```bash
git push origin feat/repl-mode
```

---

## Milestone completion criteria

- ✅ `llm_code/tui/` directory does not exist
- ✅ `tests/test_tui/` and `tests/test_e2e_tui/` directories do not exist
- ✅ `llm_code/cli/main.py` exists (not `tui_main.py`)
- ✅ `pyproject.toml` does not reference `textual`
- ✅ `[project.scripts]` points at `llm_code.cli.main:main`
- ✅ No code imports `llm_code.tui.*`
- ✅ `pytest tests/ -q` passes with 0 failures
- ✅ Interactive `llmcode` starts and responds to `/version` + `/quit`
- ✅ Branch pushed

## Risk & fallback

R4 test transliteration overrun may bite here — if fewer than expected M4–M10 tests were transliterated, the delete in 11.4 leaves visible coverage gaps. Mitigation: delete is still correct (the old tests test dead code), but the cutover commit adds a CHANGELOG note listing the coverage holes and creating follow-up issues for v2.0.1 to fill them.

## Estimated effort: ~2 hours

M11 is small by line count because it's mostly deletions. Most of the wall-time is the test suite run and interactive smoke test.

## Next milestone: M12 — Pexpect E2E Smoke Suite (`m12-smoke.md`)

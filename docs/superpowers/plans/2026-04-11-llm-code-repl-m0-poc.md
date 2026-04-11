# M0 — Proof of Concept Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate that Rich `Live` region + prompt_toolkit `Application(full_screen=False)` can coexist without redraw corruption on Warp, iTerm2, and tmux, before committing to the full v2.0.0 REPL rewrite.

**Architecture:** A standalone throwaway script at `experiments/repl_poc.py` (~300 lines) that builds a minimal prompt_toolkit Application with a status line + input area at the bottom, triggers a Rich Live streaming region above it on demand, and exits cleanly on Ctrl+D. No integration with `llm_code` runtime; purely a screen-coordination test.

**Tech Stack:** Python 3.10+, `prompt_toolkit>=3.0.47`, `rich>=13.7.0`, asyncio.

**Spec reference:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md` §10.1 R1, §4.3 (dependencies), §6.1–6.3 (coordinator/streaming design).

**Gate condition:** If the PoC fails in any of Warp / iTerm2 / tmux, **stop and escalate** — the spec may need to fall back to Strategy B (scroll-print, see §10.3 Fallback F1). Do not proceed to M1 until the gate passes and the findings are documented.

**Dependencies:** None. Runs on current `main` at `bf72f970` (v1.23.1). No changes to llmcode production code.

---

## File Structure

### New files

- `experiments/__init__.py` — package marker (empty)
- `experiments/repl_poc.py` — PoC script (~300 lines)
- `experiments/README.md` — PoC findings and instructions (~80 lines)

### Modified files

- `pyproject.toml` — add `prompt_toolkit>=3.0.47` to `[project] dependencies`

### Files NOT touched

- Everything under `llm_code/` — runtime, api, tools, tui, cli — untouched. The PoC does not import from `llm_code`.

---

## Tasks

### Task 0.1: Add prompt_toolkit dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Verify prompt_toolkit not already present**

Run: `grep -n 'prompt_toolkit\|prompt-toolkit' pyproject.toml`

Expected: no matches, or only as a transitive dep comment. If the line already exists with version `>=3.0.47`, skip to Task 0.2.

- [ ] **Step 2: Read pyproject.toml dependencies block**

Read `pyproject.toml` lines 1–60 and identify the `dependencies = [...]` list inside `[project]`.

- [ ] **Step 3: Add prompt_toolkit line**

Use the Edit tool to add `'prompt_toolkit>=3.0.47',` to the dependencies list, placed alphabetically. Example old block:

```toml
dependencies = [
    'anthropic>=0.34',
    'httpx>=0.27',
    'pydantic>=2.6',
    'tomli>=2.0 ; python_version < "3.11"',
    ...
]
```

After edit:

```toml
dependencies = [
    'anthropic>=0.34',
    'httpx>=0.27',
    'prompt_toolkit>=3.0.47',
    'pydantic>=2.6',
    'tomli>=2.0 ; python_version < "3.11"',
    ...
]
```

- [ ] **Step 4: Install in the miniconda3 venv**

Run: `/Users/adamhong/miniconda3/bin/python3 -m pip install 'prompt_toolkit>=3.0.47'`

Expected output: `Successfully installed prompt_toolkit-3.0.X` where X >= 47.

- [ ] **Step 5: Verify installation**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import prompt_toolkit; print(prompt_toolkit.__version__)"`

Expected: `3.0.47` or higher.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add prompt_toolkit>=3.0.47 for REPL mode PoC"
```

---

### Task 0.2: Create experiments directory scaffold

**Files:**
- Create: `experiments/__init__.py`
- Create: `experiments/README.md`

- [ ] **Step 1: Create the directory and marker**

Run: `mkdir -p experiments && touch experiments/__init__.py`

Expected: `ls experiments/` shows `__init__.py`.

- [ ] **Step 2: Write experiments/README.md**

Write the following content to `experiments/README.md`:

```markdown
# llm-code experiments

Throwaway prototypes for validating architecture decisions before they
land in production code. These files are NOT imported by `llm_code` and
are not shipped in the wheel.

Each experiment documents its findings inline or in a sibling `.md` file.

## Current experiments

- `repl_poc.py` — M0 proof-of-concept for the v2.0.0 REPL rewrite.
  Validates that Rich `Live` + prompt_toolkit `Application(full_screen=False)`
  can coexist on Warp, iTerm2, and tmux without redraw corruption.
  See `docs/superpowers/plans/2026-04-11-llm-code-repl-m0-poc.md` for the gate.

## Running an experiment

```bash
/Users/adamhong/miniconda3/bin/python3 experiments/<name>.py
```

## Cleanup

Once an experiment has served its purpose and the findings are captured in
a spec or plan, delete the experiment file. Keep only live experiments here.
```

- [ ] **Step 3: Commit the scaffold**

```bash
git add experiments/__init__.py experiments/README.md
git commit -m "experiment: scaffold experiments/ directory for v2.0.0 PoCs"
```

---

### Task 0.3: Write the PoC script

**Files:**
- Create: `experiments/repl_poc.py`

- [ ] **Step 1: Write the full PoC script**

Write `experiments/repl_poc.py` with the following content:

```python
"""PoC — validate Rich Live + prompt_toolkit Application coexistence.

Run: /Users/adamhong/miniconda3/bin/python3 experiments/repl_poc.py

Expected behavior:
- Intro messages print to scrollback (native terminal scrollback)
- Bottom of terminal shows a reverse-video status line + 3-line input area
- Type text, press Enter → input echoes as `> {text}` to scrollback
- Type 'stream' + Enter → Rich Live region appears above the status line
  rendering a fake streaming Markdown response (with code block),
  then commits to scrollback when done
- Scroll wheel moves terminal scrollback natively (not captured by app)
- Mouse drag-select copy works natively (not captured by app)
- Ctrl+D on empty input exits cleanly
- Terminal resize during run adapts without garbage characters

Gate: this PoC must work in Warp, iTerm2, and tmux before the spec
(docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md)
proceeds to M1.
"""
from __future__ import annotations

import asyncio
import sys

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel


# Fake state held at module level for simplicity. In production, all of
# this lives inside REPLBackend + ScreenCoordinator (see spec §6.1).
_STATE = {
    "model": "Q3.5-122B",
    "project": "llmcode-poc",
    "tokens": 0,
    "cost_usd": 0.0,
}


def _status_line_text() -> str:
    """Render the status line as a reverse-video one-liner.

    Production uses StatusLine component (spec §6.2); PoC fakes it inline.
    """
    return (
        f" {_STATE['model']} · {_STATE['project']} · "
        f"{_STATE['tokens']} tok · ${_STATE['cost_usd']:.2f} "
    )


async def _fake_stream(console: Console) -> None:
    """Emit a fake streaming Markdown response via Rich Live.

    This is the core PoC: can Rich Live refresh in place ABOVE the
    prompt_toolkit reserved area, and commit to scrollback without
    overlapping the status line or input buffer?
    """
    chunks = [
        "# Streaming test\n\n",
        "This is a **streaming Markdown** response rendered in a ",
        "Rich `Live` region above the input area.\n\n",
        "It should appear to type character-by-character, then commit ",
        "to scrollback as a clean final render.\n\n",
        "```python\n",
        "def hello(name: str) -> str:\n",
        "    return f'Hello, {name}!'\n",
        "```\n\n",
        "The code block above should be syntax-highlighted in the ",
        "final commit (though it may appear as plain text mid-stream ",
        "until the closing ``` arrives — this flicker is acceptable).",
    ]
    buffer = ""

    with Live(
        Panel(
            Markdown(buffer + "▋"),
            border_style="cyan",
            title="[dim]assistant[/dim]",
            title_align="left",
        ),
        console=console,
        refresh_per_second=10,
        transient=True,       # region clears itself on stop
        auto_refresh=True,
    ) as live:
        for chunk in chunks:
            await asyncio.sleep(0.15)
            buffer += chunk
            live.update(
                Panel(
                    Markdown(buffer + "▋"),
                    border_style="cyan",
                    title="[dim]assistant[/dim]",
                    title_align="left",
                )
            )

    # After the Live region stops (transient=True clears it), print the
    # final rendered Markdown to scrollback as permanent output.
    console.print(Markdown(buffer))

    # Update fake state
    _STATE["tokens"] += len(buffer.split())
    _STATE["cost_usd"] += 0.001


async def main() -> None:
    console = Console()

    # Intro — print to normal scrollback before the app takes the bottom
    console.print("[bold cyan]M0 PoC — REPL architecture validation[/bold cyan]")
    console.print(
        "[dim]Type anything and press Enter to echo. "
        "Type 'stream' to see a fake streaming response. "
        "Ctrl+D to exit.[/dim]"
    )
    console.print()

    input_buffer = Buffer(multiline=True)
    kb = KeyBindings()

    @kb.add("c-d")
    def _exit(event) -> None:  # type: ignore[no-untyped-def]
        event.app.exit()

    @kb.add("c-c")
    def _interrupt(event) -> None:  # type: ignore[no-untyped-def]
        # Ctrl+C clears input; second Ctrl+C on empty input exits
        if input_buffer.text:
            input_buffer.reset()
        else:
            event.app.exit()

    @kb.add("enter")
    def _submit(event) -> None:  # type: ignore[no-untyped-def]
        text = input_buffer.text.strip()
        if not text:
            return
        input_buffer.reset()
        # Schedule the async handler without blocking the key handler
        asyncio.create_task(_handle_submit(text, event.app, console))

    async def _handle_submit(text: str, app, console: Console) -> None:
        # Echo the user message to scrollback
        console.print(f"[bold green]> {text}[/bold green]")

        if text == "stream":
            await _fake_stream(console)
        elif text in {"quit", "exit", "/quit", "/exit"}:
            app.exit()

        # Trigger a redraw to refresh the status line
        app.invalidate()

    # Layout: status line (1 row, reverse video) + input area (3 rows)
    status_window = Window(
        FormattedTextControl(lambda: _status_line_text()),
        height=1,
        style="class:status",
    )
    input_window = Window(
        BufferControl(buffer=input_buffer),
        height=3,
        style="class:input",
    )

    layout = Layout(HSplit([status_window, input_window]))

    style = Style.from_dict({
        "status": "reverse",
        "input": "",
    })

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,          # KEY: don't enter alt-screen mode
        mouse_support=False,        # KEY: don't capture mouse events
        style=style,
    )

    try:
        await app.run_async()
    except (EOFError, KeyboardInterrupt):
        pass

    console.print("\n[dim]PoC exited cleanly.[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
```

- [ ] **Step 2: Verify the script has valid Python syntax**

Run: `/Users/adamhong/miniconda3/bin/python3 -c "import ast; ast.parse(open('experiments/repl_poc.py').read()); print('syntax OK')"`

Expected: `syntax OK`.

- [ ] **Step 3: Commit the PoC script**

```bash
git add experiments/repl_poc.py
git commit -m "experiment: REPL mode PoC — Rich Live + prompt_toolkit coexistence test"
```

---

### Task 0.4: Run the PoC in Warp (primary target)

**Files:** none (runtime test only)

- [ ] **Step 1: Ensure you are running inside Warp**

Confirm: the terminal window is Warp (macOS). Other terminals are Task 0.5 / 0.6.

- [ ] **Step 2: Launch the PoC**

Run: `/Users/adamhong/miniconda3/bin/python3 experiments/repl_poc.py`

Expected within ~1 second:
- Three intro lines printed in normal scrollback (cyan bold "M0 PoC…", dim "Type anything…", blank line)
- At the terminal bottom: one reverse-video status line reading ` Q3.5-122B · llmcode-poc · 0 tok · $0.00 `
- Below the status line: a 3-row empty input area with a cursor

If this basic layout is wrong (e.g., the status line is at the top, or the input is floating, or the intro messages are covered), the gate FAILS — stop and file findings in Task 0.7.

- [ ] **Step 3: Test basic input echo**

Type: `hello world` + Enter.

Expected:
- The input area clears
- Above the status line (in normal scrollback), a new line appears: `> hello world` in bold green
- Scrollback auto-scrolls so the status line + input stay visible at the bottom

- [ ] **Step 4: Test streaming**

Type: `stream` + Enter.

Expected:
- Input clears
- `> stream` echoes to scrollback
- A Rich Panel appears ABOVE the status line (not in scrollback, floating)
  - Title: `assistant`
  - Border: cyan
  - Body: Markdown that grows chunk-by-chunk over ~2 seconds
  - A `▋` cursor glyph at the end of the current buffer
- After all chunks land:
  - The Live Panel disappears (transient=True)
  - The final rendered Markdown commits to scrollback with syntax-highlighted Python code block
  - Status line updates: `tokens` > 0, `cost_usd` > $0.00

- [ ] **Step 5: Test native scrollback / text selection**

- Scroll the mouse wheel up: should scroll the terminal's native scrollback revealing earlier output. The status line + input area should stay fixed at the bottom.
- Click and drag to select text in the scrollback: should work natively (Warp highlights the selection; Cmd+C copies).

If scroll wheel does NOT scroll scrollback, or drag-select does not work, the gate FAILS — this is the core UX we're trying to enable.

- [ ] **Step 6: Test terminal resize**

While the PoC is running, resize the Warp window (drag the corner, or toggle full-screen). Expected:
- Status line stays 1 row
- Input area stays 3 rows
- No garbage characters, no misaligned borders, no stuck cursors
- Previous streaming output in scrollback remains intact

- [ ] **Step 7: Test Ctrl+D exit**

Press: Ctrl+D on empty input.

Expected:
- The prompt_toolkit Application exits cleanly
- The PoC prints `PoC exited cleanly.` and returns to the shell prompt
- Exit status 0 (verify with `echo $?`)

- [ ] **Step 8: Document Warp findings**

Note in a scratch file or memory: Warp result = PASS or FAIL. If FAIL, record which step failed and what was observed.

---

### Task 0.5: Run the PoC in iTerm2

**Files:** none (runtime test only)

- [ ] **Step 1: Launch iTerm2 and navigate to the repo**

`cd /Users/adamhong/Work/qwen/llm-code` in iTerm2.

- [ ] **Step 2: Run the PoC**

Run: `/Users/adamhong/miniconda3/bin/python3 experiments/repl_poc.py`

- [ ] **Step 3: Repeat Task 0.4 steps 2–7 in iTerm2**

Same expectations. Note any differences from Warp.

- [ ] **Step 4: Document iTerm2 findings**

Note: iTerm2 result = PASS / FAIL, plus any observed differences.

---

### Task 0.6: Run the PoC under tmux

**Files:** none (runtime test only)

- [ ] **Step 1: Start a tmux session**

Run (in any terminal): `tmux new-session -s repl-poc`

- [ ] **Step 2: Launch the PoC inside tmux**

Run: `/Users/adamhong/miniconda3/bin/python3 experiments/repl_poc.py`

- [ ] **Step 3: Repeat Task 0.4 steps 2–7 under tmux**

Same expectations. tmux is historically flaky with apps that use `full_screen=False` — if this fails, it's a known class of issue and does NOT automatically fail the gate (tmux users can still use the TUI via a fallback mode, documented in Task 0.7).

- [ ] **Step 4: Detach and reattach**

Press: Ctrl+B, then D (tmux detach).

Then: `tmux attach -t repl-poc`

Expected: the PoC resumes intact. Status line + input visible.

- [ ] **Step 5: Document tmux findings**

Note: tmux result = PASS / FAIL / PARTIAL, plus any observed behavior (e.g., resize handled but detach/reattach flickers).

---

### Task 0.7: Write the gate decision doc

**Files:**
- Create: `experiments/M0-FINDINGS.md`

- [ ] **Step 1: Write the findings document**

Write `experiments/M0-FINDINGS.md` with the structure below. Fill in `{...}` with actual observations from Tasks 0.4–0.6.

```markdown
# M0 PoC Findings — REPL Mode Architecture Gate

**Date:** {YYYY-MM-DD you ran the PoC}
**PoC script:** `experiments/repl_poc.py`
**Spec:** `docs/superpowers/specs/2026-04-11-llm-code-repl-mode-design.md`

## Environment

- macOS version: `{uname -r}`
- Python: `{python3 --version}`
- prompt_toolkit: `{pip show prompt_toolkit | grep Version}`
- rich: `{pip show rich | grep Version}`

## Per-terminal results

### Warp

- Layout correct at launch: {YES / NO}
- Input echo works: {YES / NO}
- Streaming Live region renders in place: {YES / NO}
- Streaming commits to scrollback: {YES / NO}
- Native scroll wheel works: {YES / NO}
- Native drag-select copy works: {YES / NO}
- Resize handled: {YES / NO}
- Ctrl+D exit clean: {YES / NO}
- Overall: **{PASS / FAIL / PARTIAL}**
- Notes: {any observed quirks}

### iTerm2

(same checklist as Warp)

### tmux

(same checklist as Warp)

## Gate decision

{One of:}

**PASS** — All three terminals meet the criteria. Proceed to M1 (Protocol base).

**PARTIAL** — Warp and iTerm2 pass; tmux shows {specific issue}. This does not
block the gate because tmux users can work around by {workaround}. Document
tmux behavior in the v2.0.0 release notes as a known limitation. Proceed to M1.

**FAIL** — {specific terminal(s)} show {specific issue(s)} that cannot be
worked around. The core assumption of the spec — that Rich Live + prompt_toolkit
Application(full_screen=False) coexist cleanly — is false for our target
environments. **STOP.** Do not proceed to M1. Escalate to the spec author
for one of:

1. Fall back to Strategy B scroll-print (spec §10.3 F1) and rewrite the spec
   sections on streaming rendering accordingly.
2. Investigate whether a different Python library (Textual inline mode,
   raw ANSI, or custom) can bridge the gap.
3. Accept tmux-only fallback and restrict tmux users to Strategy B.

## Recommendation

{Based on the above, one paragraph recommending the next step.}
```

- [ ] **Step 2: Commit the findings**

```bash
git add experiments/M0-FINDINGS.md
git commit -m "experiment: M0 PoC findings — {PASS|PARTIAL|FAIL} gate decision"
```

---

### Task 0.8: Gate decision — proceed to M1 or escalate

**Files:** none (decision point only)

- [ ] **Step 1: Re-read `experiments/M0-FINDINGS.md`**

Confirm the gate decision at the bottom is unambiguous.

- [ ] **Step 2: Branch on the decision**

**If PASS or PARTIAL:**
- Announce: "M0 gate passed. Proceeding to M1."
- Start work on the next plan: `docs/superpowers/plans/2026-04-11-llm-code-repl-m1-protocol.md` (to be written after M0 passes).

**If FAIL:**
- Announce: "M0 gate FAILED. Escalating to spec author."
- Stop the agent. The spec must be revised before M1 can begin.
- Do NOT make any production code changes.

- [ ] **Step 3: (If PASS/PARTIAL) Push the experiments to main**

```bash
git push origin main
```

(This is fine because `experiments/` is a standalone directory that doesn't touch `llm_code/` runtime code. main stays at v1.23.1 behavior-wise.)

---

## Milestone completion criteria

M0 is considered complete when:

- ✅ `experiments/repl_poc.py` exists and passes syntax check
- ✅ PoC has been run in Warp, iTerm2, and tmux
- ✅ `experiments/M0-FINDINGS.md` records per-terminal results
- ✅ Gate decision is explicit: PASS / PARTIAL / FAIL
- ✅ All commits are pushed to `origin/main`
- ✅ The next plan (`m1-protocol.md`) is either started (PASS/PARTIAL) or the spec is being revised (FAIL)

## Estimated effort

- Task 0.1 (dep bump): 5 minutes
- Task 0.2 (scaffold): 5 minutes
- Task 0.3 (write PoC script): 30 minutes (mostly copying the code block above)
- Task 0.4 (Warp run): 10 minutes
- Task 0.5 (iTerm2 run): 10 minutes
- Task 0.6 (tmux run): 15 minutes (includes detach/reattach)
- Task 0.7 (findings doc): 20 minutes
- Task 0.8 (gate decision): 5 minutes

**Total: ~1.5 hours** for a single focused session.

## Why this milestone exists

This milestone was added to the spec's M-list as an explicit gate (spec §10.1 R1, R2) because the architecture's highest-impact risks are R1 (Rich Live + PT Application screen contention) and R2 (PT full_screen=False mode bugs). A 1.5-hour PoC catches these risks before the team commits to writing ~3500 lines of REPL backend code that would need to be thrown out if the fundamental assumption is wrong.

If this milestone is skipped and R1/R2 surface in M3 or M6, the cost of the discovery is ~5–10× higher because the component code is already partially written. M0 is cheap insurance.

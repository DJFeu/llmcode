# llm-code Fullscreen TUI — Design Spec

**Date:** 2026-04-04
**Status:** Approved
**Goal:** Replicate Claude Code's terminal UI experience using Python Textual framework

---

## Overview

Replace the default print-scroll CLI with a fullscreen alternate-screen TUI that matches Claude Code's visual behavior: fixed header, scrollable chat area, fixed input bar, persistent status bar, inline tool/permission display.

## Architecture

### Three-Layer UI Strategy

| Mode | Framework | Launch Flag | Node.js Required |
|------|-----------|-------------|------------------|
| **Default (fullscreen)** | Python Textual | (none) | No |
| **Lite (print-scroll)** | Rich + prompt_toolkit | `--lite` | No |
| **Ink (luxury)** | React + Ink | `--ink` | Yes |

All three layers share the same `ConversationRuntime`. Only the rendering layer differs.

### Fullscreen Layout (Claude Code Style)

```
┌─ HeaderBar (1 line, dock: top) ───────────────┐
│ llm-code · qwen3.5-122b · ~/project · main    │
├─ ChatScrollView (fills remaining space) ──────┤
│ ❯ user input                                   │
│   ┌ read_file                                  │
│   │ {'path': '/src/main.py'}                   │
│   ✓ Read 45 lines                              │
│                                                 │
│   Response text...                              │
│   ✓ Done (3.2s) ↑2,400 · ↓890 tok · $0.03     │
├─ InputBar (dock: bottom) ─────────────────────┤
│ ❯ |                                            │
├─ StatusBar (dock: bottom, 1 line) ────────────┤
│ qwen3.5 │ ↓890 tok │ $0.03 │ /help │ Ctrl+D   │
└────────────────────────────────────────────────┘
```

## Widgets (7 total)

### HeaderBar
- Single line, docked to top
- Content: `llm-code · {model} · {project_dir.name} · {git_branch}`
- Style: `dim` text
- Updates on model switch or directory change

### ChatScrollView
- Scrollable container filling all space between header and input
- Auto-scrolls to bottom on new content
- User can scroll up to read history (auto-scroll pauses while scrolled up, resumes on new input)
- Contains a stream of chat entries: UserMessage, AssistantText, ToolBlock, ThinkingBlock, TurnSummary, PermissionInline

### ToolBlock
- Inline display using `┌ │ ✓/✗` line characters

**Format:**
```
  ┌ {tool_name}                          ← cyan bold
  │ {args as JSON or formatted preview}  ← dim
  ✓ {result summary}                     ← green (success) or red ✗ (error)
```

**Bash special case:**
```
  ┌ bash
  │ $ {command}                          ← white on dark background
  ✓ {output first line}
```

**Edit special case — includes diff:**
```
  ┌ edit_file
  │ {'path': '/src/main.py', ...}
  ✓ main.py +12 -3
    @@ -5,4 +5,10 @@
    - old line                           ← red
    + new line                           ← green
      context line                       ← dim
```

### ThinkingBlock
- **Default: collapsed** — shows single line: `💭 Thinking (3.2s · ~1,500 tok)`
- **Expanded**: shows thinking content (dim text, max 3000 chars)
- Toggle: click or keyboard shortcut (Ctrl+O)
- Style: `blue dim` border when collapsed, full dim text when expanded

### PermissionInline
- Inline in chat flow, not a modal dialog
- Left yellow border line for visual distinction

**Format:**
```
  ┌ bash
  │ $ rm -rf node_modules && npm install
  ▌ ⚠ Allow?  [y] Yes  [n] No  [a] Always       ← yellow left border
```

- **Single-key response**: `y`/`n`/`a` immediately processed, no Enter needed
- After response, replaced by result: `✓ allowed` or `✗ denied`
- Blocks further model execution until user responds

### InputBar
- Fixed at bottom, above StatusBar
- Prompt: `❯ ` (cyan bold)
- Features:
  - Shift+Enter: newline (multiline input)
  - Enter: submit
  - Ctrl+V / Cmd+V: paste image from clipboard
  - Tab: slash command autocomplete
  - Vim mode: toggle via `/vim`, shows `[N]` prefix in normal mode
  - Escape: cancel current generation (when model is running)
- When model is running: input disabled, shows spinner in ChatScrollView

### StatusBar
- Single line, docked to bottom (below InputBar)
- Content: `{model} │ ↓{tokens} tok │ ${cost} │ streaming… │ /help │ Ctrl+D quit`
- All text `dim`
- Dividers: `│` (dim)
- Updates in real-time during streaming
- Shows `streaming…` while model is generating
- Vim mode indicator prepended when active: `-- NORMAL -- │ ...`

## Status/Spinner Behavior

No more dead zones — user always sees what's happening:

| Phase | Display Location | Text |
|-------|-----------------|------|
| Waiting for API | ChatScrollView bottom | `⠋ Waiting for model… (Xs)` |
| Model thinking | ChatScrollView bottom | `⠋ Thinking… (Xs)` |
| Tool executing | ChatScrollView bottom | `⠋ Running {tool_name}… (Xs)` |
| After tool, before next LLM call | ChatScrollView bottom | `⠋ Processing… (Xs)` |
| Turn complete | ChatScrollView entry | `✓ Done (Xs) ↑N · ↓N tok · $X.XX` |

Spinner animation: dots cycle (⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏), updates every 100ms.
Elapsed time updates every 500ms via background timer.

## Agent Status Display

```
🤖 Agent spawned: {task description}       ← cyan bold
  ┌ read_file
  │ ...
  ✓ ...
✓ Agent complete (8.2s)                    ← green
```

or on error:
```
✗ Agent error (1.5s)                       ← red
```

## Color System

| Element | Textual Style |
|---------|--------------|
| Primary text | `white` (terminal default) |
| Prompt `❯` | `bold cyan` |
| Header | `dim` |
| Tool name | `bold cyan` |
| Tool args/lines | `dim` |
| Success `✓` | `bold green` |
| Error `✗` | `bold red` |
| Diff `+` | `green` |
| Diff `-` | `red` |
| Thinking | `dim blue` |
| Permission warning | `yellow` (border + `⚠`) |
| Spinner | `blue` |
| StatusBar | `dim` throughout |
| Bash command | `white` on `$surface-darken-1` |
| Shortcut keys `[y]` | `bold` within `dim` context |

Background: inherit terminal default (no forced black).

## File Structure

### New Files

```
llm_code/tui/
├── __init__.py
├── app.py              # Textual App — assembles all widgets, owns ConversationRuntime bridge
├── header_bar.py       # HeaderBar widget
├── chat_view.py        # ChatScrollView — scrollable chat container
├── chat_widgets.py     # ToolBlock, ThinkingBlock, PermissionInline, TurnSummary, SpinnerLine
├── input_bar.py        # InputBar — fixed bottom input with vim/autocomplete
├── status_bar.py       # StatusBar — fixed bottom status line
└── theme.py            # Color constants, Textual CSS
```

### Modified Files

- `llm_code/cli/tui_main.py` — default launches `tui.app.LLMCodeTUI`; `--lite` launches existing `tui.LLMCodeCLI`
- `pyproject.toml` — add `textual>=0.80` dependency

### Untouched Files

- `llm_code/cli/tui.py` — remains as `--lite` mode
- `llm_code/cli/ink_bridge.py` — remains as `--ink` mode
- `llm_code/runtime/` — shared by all three UI layers, no changes

## Migration Strategy

Incremental, non-breaking:

1. **Phase 1**: Skeleton — `tui/app.py` boots into fullscreen with HeaderBar + empty ChatScrollView + InputBar + StatusBar. Can type and see echo.
2. **Phase 2**: Connect Runtime — wire `ConversationRuntime`, streaming text appears in ChatScrollView.
3. **Phase 3**: Tool Display — ToolBlock renders `┌/│/✓` format for all tool types.
4. **Phase 4**: Thinking + Diff — ThinkingBlock (collapsible) + inline diff for edit_file.
5. **Phase 5**: Permission — PermissionInline with single-key y/n/a response, blocks execution.
6. **Phase 6**: StatusBar live updates — real-time tokens, cost, spinner phase.
7. **Phase 7**: Polish — Vim mode in InputBar, slash command autocomplete, image paste, scroll behavior.
8. **Phase 8**: Switch default — `tui_main.py` defaults to fullscreen; old CLI becomes `--lite`.

Each phase is independently testable and shippable.

## Dependencies

- `textual>=0.80` (Python, pip install)
- No Node.js required for default mode
- No new system dependencies

## Testing Strategy

- Unit tests for each widget (Textual's `pilot` test framework)
- Integration test: boot app, send input, verify ChatScrollView contains expected entries
- Existing 2370 runtime tests unaffected (no runtime changes)

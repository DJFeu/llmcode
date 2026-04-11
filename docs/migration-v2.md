# Migrating to llmcode v2.0.0

**v2.0.0 replaces the Textual fullscreen TUI with a line-streaming REPL
built on prompt_toolkit + Rich.** This document explains what changed,
why, and what you need to do (usually nothing).

## TL;DR

- Install works the same: `pip install -U llmcode-cli`
- The `llmcode` command works the same
- All your config, session checkpoints, memory, and prompt history
  carry over unchanged
- **Mouse drag-select-copy now works natively** — no Option+drag
  workaround, no app-owned mouse capture
- **Scroll wheel scrolls your terminal natively** — no `/scroll`
  command or `Shift+↑↓` workaround
- **Terminal Find (⌘F / Ctrl+F) works** because llmcode no longer
  takes over the full screen with an alt-screen buffer
- **No commands are removed.** All 53 slash commands carry over;
  a few changed their interactive *flow* (modal → inline print),
  but every name and every script you had still runs.

## Install

```bash
pip install -U llmcode-cli
```

That's it. The v2.0.0 package on PyPI is a drop-in upgrade.

## What changed for you

### Things that work better

1. **Native click-drag text selection.** You can drag across a tool
   result, Cmd+C / Ctrl+Shift+C, paste into another app. The v1.x
   TUI captured mouse events for its own scrollback widget, blocking
   the terminal's selection layer. v2.0 doesn't capture mouse at all.
2. **Native scroll wheel.** Your terminal's scrollback is llmcode's
   history — scroll up as far as you want. The v1.x alt-screen TUI
   destroyed scrollback on exit; v2.0 writes into your real scrollback
   and never replaces it.
3. **Terminal Find.** ⌘F (Warp / iTerm2 / macOS Terminal) and
   Ctrl+Shift+F (xterm and friends) now search the visible conversation
   because llmcode lives in line-streaming mode, not alt-screen.
4. **Warp AI block recognition.** Warp sees the ❯ prompt as a shell
   prompt and can offer its own AI block actions without conflicts.
5. **iTerm2 split panes** and **tmux copy-mode** both work correctly
   because llmcode no longer commandeers the terminal's full state.
6. **OSC8 hyperlinks** click-through in terminals that support them
   (Warp, iTerm2, WezTerm). No more copy-the-URL dance.
7. **No wheel-triggered `/voice`.** The v1.23.1 regression where
   scrolling the mouse wheel in Warp recalled `/voice` into the input
   buffer is **structurally impossible** in the new REPL — it has no
   mouse capture at all.
8. **Faster cold start.** The REPL is up in well under a second on a
   warm cache; the Textual TUI could take 2–3s on first run.

### Things that changed form

All 53 slash commands still work. These ones render differently in
v2.0.0 because the underlying Textual widgets are gone:

- **`/help`** — was a Textual three-tab modal; now an inline print
  of the built-in commands + loaded skill commands, written into
  your terminal's scrollback where you can search and copy it.
- **`/settings`** — was a Textual modal; now an inline print of the
  current settings sections. Edit fields with `/set <key> <value>`.
- **`/skill`, `/mcp`, `/plugin`** with no sub-command — was a
  Textual marketplace browser (card grid); now a plain list of
  installed + known items with a one-line usage hint. The
  `install`, `enable`, `disable`, `remove`, `list` sub-commands
  are unchanged.
- **`/theme`** — v1.x switched the Textual color theme; v2.0 honors
  the terminal's own palette (prompt_toolkit + Rich). The command
  prints a note and keeps the name discoverable.
- **`/vim`** — v1.x toggled a custom InputBar vim-mode. v2.0's input
  layer is prompt_toolkit, which has its own vim-mode implementation
  that isn't runtime-toggleable from a slash command yet. The command
  prints a note explaining this.
- **`/image`** — v1.x inserted an image marker into the Textual
  InputBar. v2.0 appends the loaded image to `state.pending_images`
  so the renderer forwards it to the next turn.
- **`/copy`** — v1.x walked the ChatScrollView widgets; v2.0 walks
  `runtime.session.messages` and copies the last assistant message
  via `pyperclip` when available. Otherwise the response is still
  selectable in the terminal directly.

### Things that stayed the same

- **Enter** submits. **Shift+Enter** (Alt+Enter) inserts a newline.
- **Ctrl+↑ / Ctrl+↓** recall prompt history.
- **Ctrl+G** toggles voice input (when voice is configured).
- **Ctrl+C** cancels the current operation without exiting.
- **Ctrl+D** on an empty buffer exits cleanly.
- **Tab** auto-completes slash commands.
- All 53 slash commands, with unchanged names and arguments.
- All tool integrations (bash, edit, read, git, LSP, IDE, MCP, …).
- Session save / load, checkpoint recovery, prompt history,
  `~/.llmcode/config.json`, plugin + skill directories.

## If you have scripts or aliases

If you have shell automation like:

```bash
# v1.x patterns (still work in v2.0)
echo "quick question" | llmcode
llmcode -q "one-shot question"
llmcode -x "shell command to explain"
llmcode --provider ollama
llmcode --preset local-qwen
llmcode --resume last
```

These continue to work unchanged. The one-shot modes (`-q`, `-x`)
never touched the TUI layer and were unaffected by the rewrite.

## Known differences from v1.23.1

- The interactive slash-popover now shows one-line completions on
  typing `/<prefix>`. The v1.x multi-row dropdown was a custom
  Textual widget and has been replaced with prompt_toolkit's
  built-in completer.
- Voice recording UI shows `🎙 {elapsed} · peak {N}` in the status
  line instead of the v1.x full-width banner. The background recorder
  path is unchanged (the M9.5 `PollingRecorderAdapter` wires the real
  `AudioRecorder` into the same event loop).
- The Textual theme switcher and its 4 built-in themes are gone;
  your terminal's palette is now authoritative.

## If you need to roll back

```bash
pip install 'llmcode-cli==1.23.1'
```

installs the last v1.x release. Your config and sessions are backward
compatible — v1.x and v2.0 read the same files.

## Architecture notes (for power users)

v2.0.0 introduces a `llm_code/view/` package containing the whole
view layer:

- `view/base.py` — `ViewBackend` ABC, the extension point for future
  platform backends (Telegram, Discord, Slack, Web in v2.1+).
- `view/types.py` — `MessageEvent`, `StatusUpdate`, the
  `StreamingMessageHandle` / `ToolEventHandle` Protocols.
- `view/dispatcher.py` — the 53 slash-command router, decoupled from
  any specific view.
- `view/stream_renderer.py` — consumes `runtime.run_turn`'s
  `AsyncIterator[StreamEvent]` and drives any `ViewBackend`.
- `view/repl/` — the first-party REPL implementation built on
  prompt_toolkit + Rich.
- `runtime/app_state.py` — the application state container that
  used to live on `LLMCodeTUI`; now a standalone dataclass
  constructible from any entry point.

The old `llm_code/tui/` package (30 files, ~9400 lines) is deleted.
If you were importing anything from `llm_code.tui.*` in a third-party
integration, you'll need to point it at the new locations — see the
commit history around the M11 cutover for the exact remapping.

The design is inspired by
[Nous Research's hermes-agent](https://github.com/nousresearch/hermes-agent)
`BasePlatformAdapter` but kept view-scoped: llmcode's runtime is
already view-agnostic, so the Protocol only needs to cover presentation.

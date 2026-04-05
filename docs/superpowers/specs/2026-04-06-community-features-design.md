# Community-Inspired Features Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** 7 features inspired by community research (Aider, DAFC, Gitingest, OpenCode)

---

## Overview

Seven independent features to bring llm-code closer to community expectations. Ordered by priority (effort vs demand).

---

## Feature 1: Real-Time Token Cost Display

**Priority:** 1 (Low effort, Very High demand)

### Problem
`CostTracker` exists and `StatusBar` has `tokens`/`cost` reactive fields, but they only update on `/cost` command — not in real-time.

### Solution
Update StatusBar immediately after every `add_usage()` call.

### Changes
- **`llm_code/tui/app.py`**: After each `_cost_tracker.add_usage()`, update `status_bar.tokens` and `status_bar.cost` with cumulative values
- **`llm_code/tui/status_bar.py`**: Format cost as `$0.0012` when > 0, `free` when provider_base_url is localhost, omit when zero

### Display format
```
sonnet │ ↓12,345 tok │ $0.0042 │ /help │ Ctrl+D quit
```
For local models:
```
qwen3.5 │ ↓12,345 tok │ free │ /help │ Ctrl+D quit
```

---

## Feature 2: Auto-Commit Checkpoint

**Priority:** 2 (Low effort, High demand)

### Problem
Agent edits files but there's no automatic git checkpoint. Users can't easily revert individual changes.

### Solution
After `write_file` or `edit_file` tool completes successfully, auto-commit the changed file.

### Changes
- **`llm_code/runtime/auto_commit.py`** (new): `auto_commit_file(path: Path, tool_name: str) -> bool`
  - Runs `git add <path> && git commit -m "checkpoint: <tool_name> <filename>"`
  - Returns False silently if not in a git repo or file is gitignored
  - Uses subprocess with 5s timeout
- **`llm_code/runtime/config.py`**: Add `auto_commit: bool = False` to `RuntimeConfig`
- **`llm_code/runtime/conversation.py`**: In post_tool_use, if config.auto_commit and tool is write/edit, call `auto_commit_file()`

### Config
```json
{ "auto_commit": true }
```

### Commit message format
```
checkpoint: write_file src/utils.py
checkpoint: edit_file llm_code/api/client.py
```

### Edge cases
- Not in a git repo: silently skip
- File is in .gitignore: silently skip
- Git index has staged changes: only `git add` the specific file, don't disturb staged state
- Commit fails (e.g., pre-commit hook): log warning, continue

---

## Feature 3: Plan/Act Mode Toggle

**Priority:** 3 (Low effort, High demand)

### Problem
No user-facing distinction between "planning" (read-only exploration) and "acting" (making changes).

### Solution
Add `/plan` slash command that toggles plan mode. In plan mode, write tools are denied.

### Changes
- **`llm_code/tui/app.py`**: Add `_plan_mode: bool = False`, handle `/plan` command
- **`llm_code/tui/status_bar.py`**: Add `plan_mode: reactive[str]` field, display `PLAN` when active
- **`llm_code/runtime/conversation.py`**: Before tool execution, if plan_mode and tool is write/edit/bash, deny with message "Plan mode: read-only. Use /plan to switch to Act mode."

### Denied tools in plan mode
- `write_file`, `edit_file`, `bash`, `git_commit`, `git_push`, `notebook_edit`

### Allowed tools in plan mode
- All read tools: `read_file`, `glob_search`, `grep_search`, `git_status`, `git_diff`, `git_log`, `lsp_*`, `tool_search`

### UX
```
> /plan
Plan mode ON — agent will explore and plan without making changes.

> /plan
Plan mode OFF — back to normal.
```

StatusBar: `PLAN │ qwen3.5 │ ↓1,234 tok │ ...`

---

## Feature 4: DAFC Dump Mode

**Priority:** 4 (Low effort, Medium demand)

### Problem
Users sometimes want to dump the entire (small) codebase into a single prompt for use with other LLMs.

### Solution
Add `/dump` slash command that concatenates all source files into a single text block with token count.

### Changes
- **`llm_code/tools/dump.py`** (new): `dump_codebase(cwd: Path, max_files: int = 200) -> DumpResult`
  - Walk cwd respecting .gitignore (use `pathspec` or parse .gitignore)
  - Skip binary files, node_modules, .venv, .git, __pycache__
  - Concatenate as: `--- file: path/to/file.py ---\n<content>\n`
  - Return `DumpResult(text, file_count, total_lines, estimated_tokens)`
  - Token estimate: `len(text) // 4` (rough approximation)
- **`llm_code/tui/app.py`**: Handle `/dump` command, show summary, copy to clipboard via `pyperclip` or write to file

### Output format
```
Dumped 42 files (3,200 lines, ~8,000 tokens)
Copied to clipboard.
```

If too large (>100k tokens): warn and ask confirmation, or write to file instead.

### Limits
- Max 200 files (configurable)
- Max 500KB total text
- Skip files > 50KB individually

---

## Feature 5: Repo Map (AST Symbol Index)

**Priority:** 5 (Medium effort, Very High demand)

### Problem
Agent has no structural overview of the codebase. It must grep/read files to understand what exists.

### Solution
Build a symbol map of the repo using Python AST (for .py) and regex fallback (for other languages).

### Changes
- **`llm_code/runtime/repo_map.py`** (new):
  - `build_repo_map(cwd: Path) -> RepoMap`
  - `RepoMap` dataclass: `files: list[FileSymbols]`
  - `FileSymbols` dataclass: `path: str, classes: list[ClassSymbol], functions: list[str]`
  - `ClassSymbol` dataclass: `name: str, methods: list[str]`
  - For `.py`: use `ast.parse()` to extract classes, methods, top-level functions
  - For `.js/.ts`: regex fallback — `class \w+`, `function \w+`, `export (const|function) \w+`
  - For other files: just list file path (no symbols)
  - Cache to `.llm-code/repo_map.json` with file mtimes; rebuild only changed files
- **`llm_code/tui/app.py`**: Handle `/map` slash command
- **`llm_code/runtime/conversation.py`**: Inject compact repo map into system prompt when available

### Compact format (injected into system prompt)
```
# Repo Map
llm_code/api/client.py: ProviderClient(from_model)
llm_code/api/openai_compat.py: OpenAICompatProvider(send_message, stream_message, _build_payload)
llm_code/runtime/ollama.py: OllamaModel(estimated_vram_gb, fits_in_vram), OllamaClient(probe, list_models)
tests/test_runtime/test_ollama.py: TestOllamaProbe, TestOllamaListModels, TestOllamaModel
```

### Token budget
- Max 2000 tokens for repo map in system prompt
- If exceeds: only include files that were recently read/modified in session

---

## Feature 6: LSP Auto-Diagnose After Edit

**Priority:** 6 (Medium effort, High demand)

### Problem
Agent writes code without knowing if it compiles/passes lint. LSP diagnostics tool exists but must be manually invoked.

### Solution
Automatically run LSP diagnostics after write/edit tools and feed errors back to the agent.

### Changes
- **`llm_code/runtime/auto_diagnose.py`** (new):
  - `async auto_diagnose(lsp_manager, file_path: str) -> list[str]`
  - Call `lsp_manager.get_diagnostics(file_uri)` for the edited file
  - Filter to error-level only (skip warnings/hints)
  - Return formatted diagnostic strings: `file.py:42: error: Name 'foo' is not defined`
- **`llm_code/runtime/conversation.py`**: After write/edit tool completes:
  1. If LSP is available and config.lsp_auto_diagnose is True
  2. Call `auto_diagnose()` for the changed file
  3. If errors found, append a system message to conversation: "LSP found errors in <file>: ..."
  4. Agent sees this in next turn and can auto-fix
- **`llm_code/runtime/config.py`**: Add `lsp_auto_diagnose: bool = True`

### Supported file types
Trigger only for files with active LSP servers (detected by extension → server mapping in lsp/detector.py)

### Rate limiting
- At most 1 diagnose call per file per turn (prevent loops)
- If agent fixes and re-triggers, only report NEW errors

---

## Feature 7: Clean Interrupt + Resume

**Priority:** 7 (Medium effort, Medium demand)

### Problem
Ctrl+C force-kills the process. No clean save, no resume message.

### Solution
Catch SIGINT gracefully: save checkpoint, show resume command, exit cleanly.

### Changes
- **`llm_code/tui/app.py`**:
  - Override `action_quit()` or add signal handler for SIGINT
  - First Ctrl+C: save checkpoint via `CheckpointRecovery.save_checkpoint()`, display session ID, set `_interrupt_pending = True`
  - Second Ctrl+C within 2s: force exit
  - Display: `Session saved. Resume with: llm-code --resume <session_id>`
  - If no active session (idle): exit immediately

### UX flow
```
[user presses Ctrl+C during agent work]

⏸ Session paused and saved.
  Resume with: llm-code --resume ses_abc123
  Press Ctrl+C again to quit immediately.

[user presses Ctrl+C again]
Goodbye.
```

---

## File Summary

| File | Action | Feature |
|------|--------|---------|
| `llm_code/tui/app.py` | Modify | F1, F3, F4, F5, F7 |
| `llm_code/tui/status_bar.py` | Modify | F1, F3 |
| `llm_code/runtime/auto_commit.py` | New | F2 |
| `llm_code/runtime/config.py` | Modify | F2, F6 |
| `llm_code/runtime/conversation.py` | Modify | F2, F3, F5, F6 |
| `llm_code/tools/dump.py` | New | F4 |
| `llm_code/runtime/repo_map.py` | New | F5 |
| `llm_code/runtime/auto_diagnose.py` | New | F6 |
| Tests for each feature | New | All |

## Not in scope

- Tree-sitter integration (use AST + regex fallback instead)
- Automatic model switching based on cost
- Auto-fix loop for LSP errors (agent decides, not forced)
- Multi-file dump with selective inclusion UI

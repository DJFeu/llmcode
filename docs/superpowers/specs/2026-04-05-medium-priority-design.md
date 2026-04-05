# LLM-Code: Medium Priority Parity Features Design Spec

**Date:** 2026-04-05
**Author:** Adam
**Status:** Draft
**Scope:** 5 medium-priority features to further close the gap with Claude Code
**Prerequisite:** High-priority features (WebFetch, WebSearch, per-agent model, plan mode, worktree backend) are complete.

---

## Overview

### Decision Summary

| Feature | Decision |
|---------|----------|
| Status line (print CLI) | Rich Live bottom bar, same data as Textual/Ink status bars |
| LLM semantic compression | New Level 5 in ContextCompressor, async LLM summarization with DreamTask-style prompt |
| MultiEdit tool | Atomic multi-file edit with pre-validation and rollback |
| Session naming + picker | Add `name` field to Session, expand SessionManager, `/session` subcommands |
| Per-arg permission rules | User-configurable regex whitelist/denylist in config, integrated into authorize() |

### Implementation Phases

```
Phase 3a (independent, parallelizable)
  ├── Feature 6: Status line (print CLI)
  ├── Feature 8: MultiEdit tool
  └── Feature 9: Session naming + picker

Phase 3b (depends on existing runtime)
  ├── Feature 7: LLM semantic compression
  └── Feature 10: Per-arg permission rules
```

---

## Feature 6: Status Line (Print CLI)

### Problem

The Textual TUI (`llm_code/tui/status_bar.py`) and Ink frontend (`ink-ui/src/components/StatusBar.tsx`) both have persistent bottom status bars showing model, tokens, cost, and streaming state. The print-based CLI (`llm_code/cli/tui.py`) only shows cost inline after each turn — no persistent status line.

### Design

Use Rich's `Live` context manager to render a persistent bottom line in the print CLI. The status line mirrors the Textual StatusBar format:

```
model-name │ ↓1,234 tok │ $0.0050 │ streaming… │ /help │ Ctrl+D quit
```

### Files

```
llm_code/cli/status_line.py    — CLIStatusLine class (Rich Live-based)
llm_code/cli/tui.py            — Integrate CLIStatusLine into print CLI loop
```

### Data Model

```python
@dataclass
class StatusLineState:
    model: str = ""
    tokens: int = 0
    cost: str = ""
    is_streaming: bool = False
    permission_mode: str = ""
    context_usage: float = 0.0  # 0.0-1.0 fraction of context window used
```

### Interface

```python
class CLIStatusLine:
    """Persistent bottom status line for the print CLI using Rich Live."""

    def __init__(self, console: Console) -> None: ...
    def update(self, **kwargs: Any) -> None:
        """Update one or more fields (model=, tokens=, cost=, is_streaming=, etc.)."""
    def start(self) -> None:
        """Begin live rendering."""
    def stop(self) -> None:
        """Stop live rendering (restores normal scrolling output)."""
```

### Integration Points

- `LLMCodeCLI._init_session()`: Create CLIStatusLine, call `start()`
- `LLMCodeCLI._run_turn()`: Update `is_streaming=True` at start, update tokens/cost at end
- `LLMCodeCLI._cleanup()`: Call `stop()`

### Decisions

1. **Rich Live vs. prompt_toolkit**: Rich Live is simpler and already a dependency. prompt_toolkit would require restructuring the entire CLI loop. Choose Rich Live.
2. **Context usage bar**: Show fraction as `[████░░░░] 45%` when context > 60% to warn users. Computed from `session.estimated_tokens() / config.compact_after_tokens`.
3. **Permission mode indicator**: Show current mode (e.g., `[prompt]`) when not in default mode.

---

## Feature 7: LLM Semantic Compression (Level 5)

### Problem

Current Level 4 (`_auto_compact`) replaces all old messages with a static placeholder `"[Previous conversation summary]\n"` — losing all context. This causes the LLM to lose track of earlier work, repeat questions, and make inconsistent decisions.

### Design

Add **Level 5** (`_llm_summarize`) to `ContextCompressor`. When Level 4 still exceeds the budget, or as a replacement for Level 4's placeholder, call the LLM to generate a real summary of the old messages.

The summary prompt reuses the DreamTask consolidation format (Summary, Modified Files, Decisions, Patterns, Open Items) since it's already proven effective.

### Files

```
llm_code/runtime/compressor.py  — Add Level 5 _llm_summarize method
llm_code/runtime/config.py      — Add CompressorConfig with llm_summarize toggle
```

### Architecture

```
compress() flow:
  Level 1: _snip_compact       (truncate oversized tool results)
  Level 2: _micro_compact       (remove stale file reads)
  Level 3: _context_collapse    (rule-based one-line summaries)
  Level 4: _auto_compact        (discard old, keep placeholder + tail)
  Level 5: _llm_summarize       (replace placeholder with LLM-generated summary)
```

### Interface

```python
class ContextCompressor:
    def __init__(
        self,
        max_result_chars: int = 2000,
        provider: LLMProvider | None = None,
        summarize_model: str = "",
    ) -> None: ...

    async def compress_async(self, session: Session, max_tokens: int) -> Session:
        """Async version of compress() that can use LLM for Level 5."""

    def _llm_summarize(self, old_messages: tuple[Message, ...], max_summary_tokens: int) -> str:
        """Generate LLM summary of old messages. Sync wrapper around async call."""
```

### Summary Prompt

```
You are a context compression agent. Given these conversation messages from a
coding session, produce a concise summary preserving:

1. What files were read, created, or modified (exact paths)
2. Key decisions made and their rationale
3. Current state of the task (what's done, what's pending)
4. Any errors encountered and how they were resolved

Be factual. Use bullet points. Target {max_tokens} tokens.
Do not include code blocks unless they represent a critical decision.
```

### Config

```python
@dataclass(frozen=True)
class CompressorConfig:
    llm_summarize: bool = False          # opt-in (uses API tokens)
    summarize_model: str = ""            # override model for summarization (default: model_routing.compaction or global)
    max_summary_tokens: int = 1000       # max output tokens for the summary
```

### Decisions

1. **Sync vs. Async**: `compress()` is currently synchronous. Add `compress_async()` as the new async entry point. The existing `compress()` remains sync (without Level 5) for backwards compatibility. The conversation loop already uses `await` so calling `compress_async()` is natural.
2. **Opt-in**: LLM summarization costs API tokens. Default `llm_summarize: false`. Users opt in via config.
3. **Model selection**: Use `model_routing.compaction` if set, otherwise the global model. Allow override via `compressor.summarize_model`.
4. **Fallback**: If the LLM call fails (timeout, API error), fall back to Level 4's static placeholder. Never block the conversation loop.
5. **Token budget**: Summary should be ≤ 1000 tokens (~4000 chars). This keeps the compressed context lean.
6. **Not Level 4 replacement**: Level 5 runs AFTER Level 4. Level 4 still does the structural work (keep tail, discard old). Level 5 replaces the placeholder with real content.

---

## Feature 8: MultiEdit Tool

### Problem

The current `edit_file` tool operates on one file at a time. Multi-file refactors require N sequential calls, each needing permission approval. If one edit fails midway, previous edits are already applied — no atomicity.

### Design

A new `multi_edit` tool that accepts a list of edits, pre-validates all of them, applies them atomically (all-or-nothing), and returns a combined diff.

### Files

```
llm_code/tools/multi_edit.py       — MultiEditTool class
llm_code/tools/edit_file.py        — Extract _apply_single_edit() helper (reuse logic)
```

### Input Schema

```python
class MultiEditInput(BaseModel):
    edits: list[SingleEdit]

class SingleEdit(BaseModel):
    path: str
    old: str
    new: str
    replace_all: bool = False
```

JSON schema exposed to LLM:
```json
{
  "type": "object",
  "properties": {
    "edits": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": { "type": "string", "description": "Absolute path to file" },
          "old": { "type": "string", "description": "Text to search for" },
          "new": { "type": "string", "description": "Replacement text" },
          "replace_all": { "type": "boolean", "default": false }
        },
        "required": ["path", "old", "new"]
      },
      "minItems": 1,
      "maxItems": 20
    }
  },
  "required": ["edits"]
}
```

### Execution Strategy

```
1. Pre-validate ALL edits:
   - Check file exists
   - Check file_protection (write allowed)
   - Check old string is found (exact or fuzzy)
   - If ANY validation fails → return error with all failures, apply nothing

2. Snapshot all target files (read current content)

3. Apply all edits sequentially:
   - If any apply fails → rollback all files to snapshot → return error

4. Return combined result with per-file diffs
```

### Decisions

1. **Max edits**: Cap at 20 per call to prevent abuse and keep the tool result readable.
2. **Permission level**: `WORKSPACE_WRITE` (same as `edit_file`). One permission prompt covers all edits.
3. **Overlay support**: Works with OverlayFS for speculative execution (same as edit_file).
4. **Reuse edit logic**: Extract the core search-replace logic from `EditFileTool.execute()` into a shared `_apply_edit()` function that both tools call.
5. **Error reporting**: Return which edits succeeded/failed with file paths and reasons, even on rollback.

---

## Feature 9: Session Naming + Picker

### Problem

Sessions are identified by 8-char hex IDs (e.g., `22986476`). Users can't tell sessions apart without loading them. No way to name, search, or pick sessions interactively.

### Design

Add optional `name` and `tags` fields to Session. Expand SessionManager with search/rename/delete. Enhance `/session` command with subcommands and an interactive picker.

### Files

```
llm_code/runtime/session.py    — Add name/tags to Session + SessionSummary, add SessionManager methods
llm_code/cli/tui.py            — Expand /session command handler
```

### Data Model Changes

```python
@dataclass(frozen=True)
class Session:
    id: str
    messages: tuple[Message, ...]
    created_at: str
    updated_at: str
    total_usage: TokenUsage
    project_path: Path
    name: str = ""                    # NEW: human-readable name
    tags: tuple[str, ...] = ()        # NEW: user-defined tags

    def rename(self, name: str) -> "Session":
        """Return new Session with updated name."""
        return dataclasses.replace(self, name=name, updated_at=_now())

    def add_tags(self, *tags: str) -> "Session":
        """Return new Session with appended tags (deduplicated)."""
        merged = tuple(dict.fromkeys(self.tags + tags))
        return dataclasses.replace(self, tags=merged, updated_at=_now())


@dataclass(frozen=True)
class SessionSummary:
    id: str
    project_path: Path
    created_at: str
    message_count: int
    name: str = ""                    # NEW
    tags: tuple[str, ...] = ()        # NEW
```

### SessionManager Extensions

```python
class SessionManager:
    def rename(self, session_id: str, name: str) -> Session: ...
    def delete(self, session_id: str) -> bool: ...
    def search(self, query: str) -> list[SessionSummary]:
        """Search sessions by name, tags, or project_path substring."""
    def get_by_name(self, name: str) -> Session | None:
        """Find session by exact name match (first match wins)."""
```

### `/session` Subcommands

| Command | Action |
|---------|--------|
| `/session` or `/session list` | List sessions with names, show interactive picker |
| `/session save [name]` | Save current session, optionally with a name |
| `/session load <id\|name>` | Load a session by ID or name |
| `/session rename <name>` | Rename current session |
| `/session delete <id\|name>` | Delete a session (with confirmation) |
| `/session search <query>` | Search sessions by name/tags/path |
| `/session tag <tag1> [tag2...]` | Add tags to current session |

### Session Picker Display

```
Sessions (5 items)

    1 ● auth-refactor         · /Users/adam/Work/myapp (42 msgs, 2h ago)
    2 ● llm-code-parity       · /Users/adam/Work/qwen  (18 msgs, 5h ago)
    3 ○ 8a3f2c01              · /Users/adam/Work/other (3 msgs, 2d ago)
    4 ○ debugging-issue-123   · /Users/adam/Work/myapp (8 msgs, 3d ago)
    5 ○ a7b9e4d2              · /Users/adam/Work/qwen  (1 msg, 1w ago)

Enter number to select, or press Enter to cancel.
Pick #:
```

- `●` = current project, `○` = other project
- Named sessions show name, unnamed show hex ID
- Sorted by most recently updated

### Decisions

1. **Auto-naming**: Not in this iteration. Manual naming only. (Auto-naming via LLM is a future enhancement.)
2. **Backwards compatibility**: `name` and `tags` default to empty. Old session JSON files without these fields load fine via `from_dict()` with `.get()` defaults.
3. **Unique names**: Names are NOT required to be unique. `get_by_name()` returns first match (most recent). Users can use IDs for disambiguation.
4. **Delete confirmation**: `/session delete` prompts "Delete session X? (y/N)" before proceeding.
5. **Picker reuse**: Use the existing `_interactive_pick()` pattern from `tui.py`.

---

## Feature 10: Per-Arg Permission Rules

### Problem

The bash tool has a hardcoded safety classifier (`classify_command()`) with 21 rules. Users can't customize which commands are auto-allowed or blocked. The `allowed_tools` / `denied_tools` config only works at the tool level, not the argument level.

Example: A user wants `git push` to always be allowed but `git push --force` to require confirmation. Currently impossible without code changes.

### Design

Add a `bash_rules` config section with user-defined regex patterns that override the hardcoded classification. Rules are evaluated before the built-in classifier.

### Files

```
llm_code/runtime/config.py        — Add BashRulesConfig
llm_code/tools/bash.py            — Integrate user rules into classify_command()
```

### Config

```python
@dataclass(frozen=True)
class BashRule:
    pattern: str          # regex pattern matched against the full command
    action: str           # "allow" | "confirm" | "block"
    description: str = "" # optional human-readable reason

@dataclass(frozen=True)
class BashRulesConfig:
    rules: tuple[BashRule, ...] = ()
```

Example config.json:
```json
{
  "bash_rules": [
    { "pattern": "^git\\s+(push|pull)\\b(?!.*--force)", "action": "allow", "description": "Allow non-force git push/pull" },
    { "pattern": "^git\\s+push\\s+--force", "action": "confirm", "description": "Force push requires confirmation" },
    { "pattern": "^npm\\s+(install|ci|test|run)\\b", "action": "allow", "description": "Allow safe npm commands" },
    { "pattern": "^docker\\s+system\\s+prune", "action": "block", "description": "Block docker prune" }
  ]
}
```

### Classification Flow

```
classify_command(command, user_rules):
  1. Check user rules (in order):
     - First matching rule wins
     - Map "allow" → "safe", "confirm" → "needs_confirm", "block" → "blocked"
     - Return BashSafetyResult with rule_ids=["user:0"] etc.

  2. If no user rule matched, fall through to built-in classifier:
     - Existing 21 rules (R1-R21) unchanged
```

### Integration with Permissions

The existing flow already handles this correctly:
- `classify_command()` returns `BashSafetyResult`
- `BashTool.is_read_only(args)` checks `result.is_safe`
- `BashTool.is_destructive(args)` checks `result.needs_confirm or result.is_blocked`
- Conversation loop maps these to `PermissionLevel` and calls `authorize()`

The only change is `classify_command()` checking user rules first.

### Decisions

1. **Regex, not glob**: Regexes are more expressive for command matching. Compile patterns at config load time for performance.
2. **User rules first**: User rules override built-in rules. This lets users relax or tighten specific commands.
3. **No built-in rule override**: Built-in R1-R21 rules are NOT removed. User rules take precedence only when they match. If no user rule matches, built-in rules apply as before.
4. **Rule ID format**: User rules get `rule_ids=("user:N",)` where N is the 0-based index. Built-in rules keep `R1`-`R21`.
5. **Validation**: Compile regex at config load time. Invalid regex → config error with line number.
6. **Scope**: This feature only applies to the bash tool. Other tools' permission levels are unchanged.

---

## Cross-Cutting Concerns

### Testing Strategy

Each feature needs:
- Unit tests for new classes/functions
- Integration tests for config loading
- Print CLI features need manual smoke testing (Rich Live is hard to unit test)

### Config Schema Updates

`ConfigSchema` (Pydantic) needs new optional fields:
```python
class ConfigSchema(BaseModel):
    # ... existing fields ...
    compressor: dict = {}      # Feature 7
    bash_rules: list = []      # Feature 10
```

### Backwards Compatibility

All new fields have defaults. Existing configs work without changes:
- Session: `name=""`, `tags=()`
- Compressor: `llm_summarize=False`
- BashRules: empty list → no user rules → existing behavior
- StatusLine: always shown in print CLI (no config needed)

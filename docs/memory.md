# Memory System

llmcode implements a **5-layer memory architecture** that gives the LLM persistent context across turns and sessions, while keeping each layer focused on a specific job. This is one of llmcode's biggest differences from other agents.

## Layers at a glance

```
L0  Governance        Project rules        Permanent       Always loaded
L1  Working           Current task         Ephemeral       In-memory only
L2  Project           Long-term knowledge  Persistent      DreamTask consolidates
L3  Task              State machine        Cross-session   PLAN→DO→VERIFY→CLOSE
L4  Summary           Past sessions        Persistent      Compressed history
```

Plus the new typed memory taxonomy (v1.1.0+):

```
Typed   user / feedback / project / reference   MEMORY.md index
```

---

## L0 — Governance Layer

**Purpose**: Project-wide rules the LLM must follow on every turn.

**Sources** (in priority order):
1. `.llmcode/governance.md` (priority 10)
2. `.llmcode/rules/*.md` (priority 5)
3. `CLAUDE.md` / `AGENTS.md` (priority 1)

**Loaded**: Every turn, into the system prompt as the highest-priority section.

**When to use**: Hard constraints that must never be violated.

**Example** (`.llmcode/governance.md`):
```markdown
# Governance Rules

## security
- Never commit API keys or secrets
- Never run rm -rf without confirmation
- Never modify .env files

## style
- All public functions need docstrings
- Use type hints on every function signature
- snake_case for files, PascalCase for classes
```

The LLM sees these in every system prompt as `## Governance Rules` and they cannot be overridden by the conversation.

---

## L1 — Working Layer

**Purpose**: Scratch space for the current task.

**Lifetime**: Cleared at session end. Not persisted.

**Implementation**: Lives in `ConversationRuntime` as in-memory state. Each turn it accumulates tool results, file reads, and partial reasoning.

**When to use**: Anything that's only relevant to *right now*.

**Example**: When the agent reads `auth.py`, the file content sits in L1 until the turn ends. Next session, that content is gone unless something elevated it to L2 (DreamTask).

You don't manually write to L1 — it happens automatically as the runtime processes tool calls.

---

## L2 — Project Memory

**Purpose**: Long-term knowledge about the current project that the agent has accumulated.

**Storage**: `~/.llmcode/<project_hash>/memory.json` (legacy key-value format)
plus `~/.llmcode/<project_hash>/typed/` (4-type taxonomy, v1.1.0+).

**Lifetime**: Permanent until you delete it.

**How it grows**: Three ways
1. **Manual**: `/memory store key value` from the TUI
2. **LLM tools**: `memory_store`, `memory_recall`, `memory_list` are available to the agent
3. **DreamTask consolidation**: On session exit, llmcode runs `DreamTask` which extracts notable facts from the conversation and writes them to L2

**Loaded**: Every turn, the system prompt gets a `## Project Memory` section with all entries.

**Typed memory (v1.1.0+)**: Same storage area but with 4-type classification:

| Type | Purpose | Examples |
|------|---------|----------|
| `user` | User preferences and personality | "User prefers terse responses, no emoji" |
| `feedback` | Lessons from past interactions | "Don't suggest pip install — user uses uv" |
| `project` | Architecture / conventions | "Auth uses PASETO, not JWT" |
| `reference` | External knowledge | "Uses anthropics/claude-plugins-official marketplace" |

A `MEMORY.md` index file (max 200 lines) lives at the root of the typed directory and is auto-rebuilt on every write. Each typed entry has a 25KB hard limit and is rejected if it looks like derivable content (git log output, code dumps, file path lists).

**Example**:
```bash
# In TUI
/memory store stack "Python 3.11, FastAPI, PostgreSQL via asyncpg"
/memory recall stack
```

The next session, the LLM sees `**stack**: Python 3.11, FastAPI, PostgreSQL via asyncpg` in its system prompt automatically.

---

## L3 — Task Memory

**Purpose**: Track multi-turn engineering tasks across sessions with explicit state.

**Storage**: `.llmcode/tasks/*.json` (per-task JSON files in the project).

**Lifetime**: Until the task completes or is explicitly deleted.

**State machine**:
```
PLAN → DO → VERIFY → CLOSE → DONE
              |
        BLOCKED (any stage)
```

- **PLAN**: Task is being designed, requirements gathered
- **DO**: Active execution, files being changed
- **VERIFY**: Tests / lint / type checks running
- **CLOSE**: Final cleanup, summary written
- **DONE**: Terminal state — task is finished
- **BLOCKED**: Waiting on external input or unresolved error

**When to use**: Anything that takes more than one session to complete. The agent can pause, you can quit, you come back later, the task picks up where it left off.

**Example**:
```bash
# Session 1
/task new "Implement OAuth2 flow"   # creates task in PLAN state
... work happens ...
# you exit before finishing — task stays in DO state

# Session 2 (next day)
/task list   # shows the OAuth2 task still in DO state
... continue where you left off ...
/task verify <id>   # runs auto-checks
/task close <id>    # marks DONE
```

Background indicator: when the status bar shows `2 tasks running`, that's L3 polling for active tasks (PLAN/DO/VERIFY).

---

## L4 — Summary Layer

**Purpose**: Compressed history of past sessions for continuity.

**Storage**: `~/.llmcode/<project_hash>/sessions/<timestamp>.md`

**Lifetime**: Permanent.

**How it grows**: At session end, `MemoryStore.save_session_summary()` writes a markdown summary of what happened. DreamTask can also produce consolidated daily/weekly summaries in `consolidated/<date>.md`.

**Loaded**: At session start, the most recent N summaries are injected into the system prompt as `## Recent Sessions` so the LLM has context for "what we were doing last time".

**When to use**: You don't manage this directly — it happens automatically. The point is that every new session starts with knowledge of recent work.

---

## DreamTask: How L1 promotes to L2

After every session ends, `DreamTask.consolidate()` runs. It:

1. Reads the just-finished session
2. Asks the LLM (with a small budget) to extract notable facts
3. Filters for facts that pass `memory_validator.validate_content()` — rejects derivable content like git log output or pure code dumps
4. Writes survivors to L2 typed memory with appropriate type tags
5. Appends a session summary to L4

This is the only automatic memory consolidation. It's bounded to 5-30 seconds (timeout) and is completely best-effort — if it fails, the session still exits cleanly.

You can disable it: `dream.enabled = false` in config.

---

## Skill router and memory

The 3-tier skill router (v1.1.0) is technically separate from the memory layers but uses them:

- **Tier A (keyword)**: Reads skill metadata from L0 governance / project skills
- **Tier B (TF-IDF)**: Index built once at startup, cached in memory
- **Tier C (LLM classifier)**: Optional, makes one extra LLM call

When a skill matches, its content is injected into the next turn's system prompt as a `## Active Skill` section. This is a one-shot injection — the skill content goes into L1 working memory, not persistent L2.

---

## When to use which layer

| Need | Use |
|------|-----|
| Hard rules the LLM must always follow | L0 governance (`CLAUDE.md` or `.llmcode/governance.md`) |
| Project conventions, architecture facts | L2 typed `project` |
| User preferences ("don't add comments") | L2 typed `user` or `feedback` |
| External library knowledge | L2 typed `reference` |
| Multi-session task state | L3 task |
| Past session summaries | L4 (automatic) |
| Today's scratch | L1 (automatic) |

---

## Inspecting memory

```bash
/memory                  # show all current entries
/memory store k v        # set a key
/memory recall k         # get a key
/memory list             # list all keys
/memory consolidate      # force DreamTask to run now
```

Files:
```
~/.llmcode/<project_hash>/
├── memory.json                    # L2 legacy key-value
├── typed/
│   ├── MEMORY.md                  # auto-built index
│   └── topics/                    # individual typed entries
│       ├── user_role.md
│       ├── feedback_no_comments.md
│       └── ...
├── sessions/                      # L4 session summaries
│   └── 2026-04-07T13-30-00.md
└── consolidated/                  # L4 dream-consolidated summaries
    └── 2026-04-07.md

.llmcode/tasks/                    # L3 task state (in project)
└── task-<id>.json
```

---

## Why 5 layers, not 1

Most agents have a single bag of "context" mixed together. llmcode separates them because they have very different lifetimes and access patterns:

- **L0** is read-only and rule-like — must be enforced every turn
- **L1** is volatile and large — must be cleared between sessions
- **L2** is curated and stable — must persist and be validated
- **L3** is stateful — must support transitions and resumption
- **L4** is historical and compressed — must scale without bloating context

Mixing them leads to either too much noise (everything in one bag) or too little persistence (only the current turn). The 5-layer split lets each layer do one job well.

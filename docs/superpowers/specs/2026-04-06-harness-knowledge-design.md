# Harness Engine + Knowledge Compiler + Memory Lint Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** 3 features inspired by Karpathy (LLM Knowledge Bases) and Martin Fowler (Harness Engineering)
**Priority Order:** Harness Engine → Knowledge Compiler → Memory Lint

---

## Feature 1: Harness Engine (Highest Priority)

### Problem
llm-code has several quality control features that work independently:
- LSP auto-diagnose (F6) — runs after write/edit
- Code analysis engine — `/analyze`, `/diff-check`
- Auto-commit checkpoint (F2) — commits after write/edit
- Repo map (F5) — injects symbol tree into system prompt
- Plan mode (F3) — denies write tools

These are scattered across `conversation.py`, `app.py`, and individual modules with no unified framework. Users can't easily see what's active, configure them as a group, or get project-type-specific defaults.

### Solution
Unify all quality controls into a **Harness** framework with two categories:
- **Guides** (feedforward) — prevent errors before they happen
- **Sensors** (feedback) — detect errors after tool execution

### Architecture

```
llm_code/harness/
├── __init__.py
├── engine.py        # HarnessEngine — orchestrates guides + sensors
├── guides.py        # Guide implementations (repo map, architecture doc, plan mode)
├── sensors.py       # Sensor implementations (LSP, analysis rules, test runner)
├── templates.py     # Project-type harness templates
└── config.py        # HarnessConfig dataclass
```

### HarnessEngine

```python
@dataclass(frozen=True)
class HarnessControl:
    name: str
    category: str          # "guide" | "sensor"
    kind: str              # "computational" | "inferential"
    enabled: bool = True
    trigger: str = "post_tool"  # "pre_tool" | "post_tool" | "pre_turn" | "post_turn" | "on_demand"

class HarnessEngine:
    def __init__(self, config: HarnessConfig) -> None: ...
    def pre_turn(self, context) -> list[str]:
        """Run guides before each turn. Returns strings to inject into system prompt."""
    def post_tool(self, tool_name: str, file_path: str, result) -> list[HarnessFinding]:
        """Run sensors after tool execution. Returns findings for agent context."""
    def status(self) -> dict:
        """Return current harness state for /harness command."""
```

### Guides (Feedforward)

| Guide | Trigger | Source | What it injects |
|-------|---------|--------|-----------------|
| `repo_map` | pre_turn | `repo_map.py` | Symbol tree into system prompt |
| `architecture_doc` | pre_turn | `.llm-code/architecture.md` | Architecture description if exists |
| `analysis_context` | pre_turn | last `/analyze` result | Violation summary |
| `plan_mode` | pre_tool | `_plan_mode` flag | Tool denial for writes |

### Sensors (Feedback)

| Sensor | Trigger | Source | What it reports |
|--------|---------|--------|-----------------|
| `lsp_diagnose` | post_tool (write/edit) | `auto_diagnose.py` | Type/lint errors |
| `code_rules` | post_tool (write/edit) | `analysis/engine.py` | Rule violations in changed file |
| `auto_commit` | post_tool (write/edit) | `auto_commit.py` | Checkpoint commit |
| `test_runner` | post_tool (write/edit) | `pytest` subprocess | Test failures (optional, opt-in) |

### Harness Templates

Auto-detected by scanning project files:

| Template | Detection | Default Guides | Default Sensors |
|----------|-----------|---------------|-----------------|
| `python-cli` | `pyproject.toml` + no `app/` | repo_map, analysis_context | lsp, code_rules, auto_commit |
| `python-web` | `fastapi`/`flask`/`django` in deps | repo_map, architecture_doc | lsp, code_rules, test_runner |
| `node-app` | `package.json` | repo_map | code_rules |
| `monorepo` | `pnpm-workspace.yaml` / `turbo.json` | repo_map, architecture_doc | code_rules |
| `generic` | fallback | repo_map | code_rules |

### `/harness` Slash Command

```
> /harness
Harness: python-cli (auto-detected)

  Guides (feedforward):
    ✓ repo_map          pre_turn     computational
    ✓ analysis_context   pre_turn     computational
    ✗ architecture_doc   pre_turn     computational  (no .llm-code/architecture.md)
    ✓ plan_mode          pre_tool     computational  (OFF)

  Sensors (feedback):
    ✓ lsp_diagnose       post_tool    computational
    ✓ code_rules         post_tool    computational
    ✓ auto_commit        post_tool    computational
    ✗ test_runner         post_tool    computational  (opt-in: /harness enable test_runner)

> /harness enable test_runner
> /harness disable auto_commit
> /harness template python-web
```

### Integration with Existing Code

The HarnessEngine replaces the scattered hooks in `conversation.py`:
- Current: `if config.auto_commit and tool in write_tools: ...` (line ~820)
- Current: `if config.lsp_auto_diagnose and tool in write_tools: ...` (line ~850)
- Current: repo map injection in system prompt (line ~293)
- New: `harness.post_tool(tool_name, file_path, result)` — one call, engine decides what to run

### Config

```json
{
  "harness": {
    "template": "auto",
    "controls": {
      "test_runner": { "enabled": true, "command": "pytest {file} -x -q" },
      "auto_commit": { "enabled": false }
    }
  }
}
```

### Not in scope (Feature 1)
- Inferential sensors (LLM-as-judge) — future enhancement
- Custom user-defined controls — use config to enable/disable built-ins
- Pre-commit git hooks generation — separate feature

---

## Feature 2: Knowledge Compiler (High Priority)

### Problem
DreamTask consolidates session data on exit, but it only records "what happened." It doesn't build **understanding** of the project. Each new session starts from scratch, re-reading files to understand architecture, patterns, and conventions.

### Solution
A Knowledge Compiler that incrementally builds and maintains a structured project knowledge base. Inspired by Karpathy's 4-phase pipeline (Ingest → Compile → Query → Lint).

### Architecture

```
llm_code/runtime/knowledge_compiler.py

.llm-code/knowledge/
├── index.md              # Entry point: module list with one-line descriptions
├── architecture.md       # Auto-generated architecture overview
├── modules/
│   ├── api.md            # What the API layer does, key types, patterns
│   ├── runtime.md        # Runtime engine description
│   └── tools.md          # Tool system description
├── decisions.md          # Key technical decisions and rationale
└── patterns.md           # Recurring code patterns in this project
```

### Compilation Pipeline

```
Phase 1: Ingest (automatic)
  - Source: git diff from last compilation, DreamTask output, session conversations
  - Trigger: session end (after DreamTask)

Phase 2: Compile (LLM-powered)
  - Read existing knowledge files + new facts from ingest
  - Merge: update existing articles, don't overwrite
  - Create new module articles when new directories appear
  - Update index.md with any new entries
  - Update architecture.md if structural changes detected

Phase 3: Query (runtime)
  - On session start: inject relevant knowledge into system prompt
  - Use HIDA classification to select which knowledge files to load
  - Max budget: 3000 tokens for knowledge context

Phase 4: Lint (on-demand)
  - /memory lint (see Feature 3)
```

### Key Types

```python
@dataclass(frozen=True)
class KnowledgeEntry:
    path: str           # relative to .llm-code/knowledge/
    title: str
    summary: str        # one-line for index
    last_compiled: str  # ISO timestamp
    source_files: tuple[str, ...]  # which source files this knowledge covers

class KnowledgeCompiler:
    def __init__(self, cwd: Path, llm_provider: LLMProvider) -> None: ...

    async def compile(self, facts: list[str], changed_files: list[str]) -> None:
        """Incrementally update knowledge base from new facts and file changes."""

    def query(self, task_type: str) -> str:
        """Return relevant knowledge for the given HIDA task type."""

    def get_index(self) -> list[KnowledgeEntry]:
        """Return all knowledge entries."""
```

### Compilation Strategy

- **Incremental:** Only process changed files + new DreamTask facts
- **Merge, don't overwrite:** When updating a module article, keep existing content and append/update sections
- **LLM-powered:** Use the configured model (or model_routing.compaction model) to generate summaries
- **Fallback:** If no LLM available (e.g., local model not running), skip compilation silently
- **Cost:** Use the cheapest model available (compaction model from config)

### Integration

- After DreamTask runs on session end → trigger `compiler.compile()`
- On session start → `compiler.query(hida_task_type)` → inject into system prompt
- New slash command: `/knowledge` to view the knowledge base
- New slash command: `/knowledge rebuild` to force full recompilation

### Config

```json
{
  "knowledge": {
    "enabled": true,
    "compile_on_exit": true,
    "max_context_tokens": 3000,
    "compile_model": ""
  }
}
```

### Not in scope (Feature 2)
- Web clipper / external document ingestion
- Cross-project knowledge sharing
- Vector database / embeddings
- Interactive Q&A over knowledge base (agent already does this)

---

## Feature 3: `/memory lint` (Medium Priority)

### Problem
Project memory accumulates over time but is never validated. Stale entries reference deleted files, contradictory entries coexist, and important modules lack coverage.

### Solution
A memory health check command that scans project memory for issues.

### Checks

| Check | Type | What it catches |
|-------|------|----------------|
| **Stale references** | Computational | Memory mentions file/function that no longer exists |
| **Contradictions** | Inferential (LLM) | Two memories say opposite things about same topic |
| **Coverage gaps** | Computational | Source directories with no related memory/knowledge |
| **Orphan memories** | Computational | Memories that reference nothing in current codebase |
| **Age check** | Computational | Memories older than N days that haven't been validated |

### Architecture

```
llm_code/runtime/memory_lint.py

class MemoryLintResult:
    stale: list[StaleReference]       # file_path in memory doesn't exist
    contradictions: list[Contradiction] # two memories conflict (LLM-detected)
    coverage_gaps: list[str]          # directories with no memory coverage
    orphans: list[str]               # memory files with no codebase reference
    old: list[str]                   # memories older than 30 days

def lint_memory(
    memory_dir: Path,
    cwd: Path,
    llm_provider: LLMProvider | None = None,
) -> MemoryLintResult:
```

### `/memory lint` Output

```
## Memory Health Check

  STALE   memory/api_patterns.md:3     References "llm_code/api/anthropic.py" — file deleted
  STALE   memory/old_feature.md:1      References function "parse_ink_command" — not found
  GAP     llm_code/analysis/           No memory coverage for analysis package
  GAP     llm_code/harness/            No memory coverage for harness package
  OLD     memory/project_v2.md         Last updated 45 days ago
  
  Contradictions: (requires LLM, skipped — use /memory lint --deep)

Summary: 2 stale, 2 gaps, 1 old, 0 contradictions
```

### Modes

- `/memory lint` — fast, computational checks only (stale, gaps, orphans, age)
- `/memory lint --deep` — includes LLM contradiction detection (uses compaction model)
- `/memory lint --fix` — auto-remove stale references, prompt for each

### Integration

- Standalone slash command in `app.py`
- Can also be triggered by Knowledge Compiler's Lint phase
- No automatic execution (user-initiated only)

### Config

No config needed — uses existing memory directory paths.

---

## Implementation Order

```
Session N+1: Feature 1 — Harness Engine
  Task 1: HarnessConfig + HarnessControl types
  Task 2: Guide implementations (wrap existing repo_map, analysis_context, plan_mode)
  Task 3: Sensor implementations (wrap existing lsp_diagnose, code_rules, auto_commit)
  Task 4: HarnessEngine orchestration
  Task 5: Template detection
  Task 6: /harness slash command
  Task 7: Refactor conversation.py to use HarnessEngine (replace scattered hooks)

Session N+2: Feature 2 — Knowledge Compiler
  Task 1: KnowledgeEntry types + file structure
  Task 2: Compilation pipeline (incremental merge)
  Task 3: Query (context injection on session start)
  Task 4: Integration with DreamTask
  Task 5: /knowledge slash command

Session N+3: Feature 3 — Memory Lint
  Task 1: Computational checks (stale, gaps, orphans, age)
  Task 2: LLM contradiction detection (--deep)
  Task 3: /memory lint slash command + --fix mode
```

## File Summary

| File | Action | Feature |
|------|--------|---------|
| `llm_code/harness/__init__.py` | Create | F1 |
| `llm_code/harness/engine.py` | Create | F1 |
| `llm_code/harness/guides.py` | Create | F1 |
| `llm_code/harness/sensors.py` | Create | F1 |
| `llm_code/harness/templates.py` | Create | F1 |
| `llm_code/harness/config.py` | Create | F1 |
| `llm_code/runtime/conversation.py` | Modify | F1 (refactor to use HarnessEngine) |
| `llm_code/runtime/config.py` | Modify | F1, F2 |
| `llm_code/tui/app.py` | Modify | F1, F2, F3 |
| `llm_code/runtime/knowledge_compiler.py` | Create | F2 |
| `llm_code/runtime/memory_lint.py` | Create | F3 |
| Tests for each | Create | All |

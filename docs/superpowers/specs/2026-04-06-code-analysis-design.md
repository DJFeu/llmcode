# Code Analysis Features Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** 3 features — /analyze, /diff-check, code rules engine
**Inspired by:** TrueCourse (truecourse-ai/truecourse)

---

## Overview

Add deterministic code analysis to llm-code. Zero LLM calls, zero external dependencies. Python AST for .py files, regex for JS/TS, regex for universal rules. Results shown in chat AND injected into agent context.

---

## Feature A: `/analyze` — Architecture Health Check

### Trigger
`/analyze [path]` — defaults to cwd.

### Rules

| Rule Key | Name | Language | Type | Severity |
|----------|------|----------|------|----------|
| `circular-import` | Circular import chain | Python | AST | high |
| `bare-except` | Bare except clause | Python | AST | high |
| `empty-except` | Empty except/catch block | Python, JS/TS | AST/regex | medium |
| `hardcoded-secret` | Hardcoded secret | Universal | regex | critical |
| `todo-fixme` | TODO/FIXME comment | Universal | regex | low |
| `god-module` | God module (>800 lines) | Universal | file stat | medium |
| `unused-import` | Unused import | Python | AST | low |
| `print-in-prod` | print()/console.log in non-test | Python, JS/TS | AST/regex | low |
| `star-import` | Wildcard import | Python | AST | low |

### Output Format (chat)

```
## Code Analysis — 42 files, 7 violations

  CRITICAL  src/config.py:12      Hardcoded secret: API_KEY = "sk-..."
  HIGH      src/utils.py:3→45     Circular import: utils → helpers → utils
  HIGH      src/api.py:88         Bare except clause
  MEDIUM    src/api.py:92         Empty except block
  MEDIUM    src/engine.py         God module (1,200 lines)
  LOW       src/main.py:5         TODO: refactor this
  LOW       src/debug.py:14       print() in production code

Summary: 1 critical, 2 high, 2 medium, 2 low
```

### Context Injection
After analysis, compressed results are appended to the next turn's system prompt so the agent can see and optionally fix violations.

Format injected:
```
[Code Analysis] 7 violations found:
- CRITICAL src/config.py:12 Hardcoded secret
- HIGH src/api.py:88 Bare except
...
```

Max 1000 tokens for injected context. If exceeds, only include CRITICAL and HIGH.

### Changes
- **`llm_code/tui/app.py`**: Add `_cmd_analyze()` handler
- **`llm_code/analysis/engine.py`**: Main entry point `run_analysis()`

---

## Feature B: `/diff-check` — Analyze Only Changed Files

### Trigger
`/diff-check` — no arguments.

### Flow
1. Run `git diff --name-only` + `git diff --cached --name-only` to get all changed files
2. Filter to supported extensions (.py, .js, .ts, .jsx, .tsx)
3. Run code rules only on these files
4. Compare with last `/analyze` results (cached in `.llm-code/last_analysis.json`)
5. Label each violation as NEW or FIXED

### Output Format

```
## Diff Check — 3 files changed, 2 new violations, 1 resolved

  NEW   HIGH   src/api.py:88    Bare except clause
  NEW   LOW    src/api.py:5     TODO: fix later
  FIXED MEDIUM src/utils.py:30  Empty except (was line 28)
```

### Cache
- `/analyze` saves results to `.llm-code/last_analysis.json`
- `/diff-check` reads this cache for comparison
- Cache format: `{"timestamp": "...", "violations": [...]}`

### Changes
- **`llm_code/tui/app.py`**: Add `_cmd_diff_check()` handler
- **`llm_code/analysis/engine.py`**: Add `run_diff_check()` function
- **`llm_code/analysis/cache.py`**: Save/load analysis results

---

## Feature C: Code Rules Engine

### Package Structure

```
llm_code/analysis/
├── __init__.py
├── engine.py           # run_analysis(), run_diff_check()
├── rules.py            # Rule/Violation/AnalysisResult types + registry
├── python_rules.py     # Python AST rules (circular import, bare except, etc.)
├── js_rules.py         # JS/TS regex rules (empty catch, console.log, etc.)
├── universal_rules.py  # Language-agnostic regex rules (secrets, TODO, god module)
├── cache.py            # Save/load analysis results to JSON
```

### Core Types

```python
@dataclass(frozen=True)
class Violation:
    rule_key: str       # e.g. "bare-except"
    severity: str       # "critical" | "high" | "medium" | "low"
    file_path: str      # relative to cwd
    line: int           # 1-based, 0 if N/A (god-module)
    message: str        # human-readable description
    end_line: int = 0   # optional range end

@dataclass(frozen=True)
class Rule:
    key: str
    name: str
    severity: str
    languages: tuple[str, ...]  # ("python",) or ("javascript", "typescript") or ("*",)
    check: Callable             # (file_path, content, ast_tree?) -> list[Violation]

@dataclass(frozen=True)
class AnalysisResult:
    violations: tuple[Violation, ...]
    file_count: int
    duration_ms: float
```

### Rule Implementation Details

#### Python AST Rules (`python_rules.py`)

**circular-import:**
- Parse all .py files' imports via `ast.Import` / `ast.ImportFrom`
- Build directed graph of module → module dependencies
- Detect cycles with DFS
- Report: `"Circular import: a → b → c → a"`

**bare-except:**
- Walk AST for `ast.ExceptHandler` where `handler.type is None`
- Report line number

**empty-except:**
- Walk AST for `ast.ExceptHandler` where body is only `pass` or empty
- Report line number

**unused-import:**
- Collect all imported names from `ast.Import` / `ast.ImportFrom`
- Walk all `ast.Name` nodes to find used names
- Report imported names that are never referenced
- Skip `__init__.py` files (re-exports)

**star-import:**
- Walk `ast.ImportFrom` where `names[0].name == "*"`

**print-in-prod:**
- Walk `ast.Call` where func is `ast.Name(id="print")`
- Skip files in `tests/` directory

#### JS/TS Regex Rules (`js_rules.py`)

**empty-catch:**
- Pattern: `catch\s*\([^)]*\)\s*\{\s*\}` (catch with empty body)

**console-log:**
- Pattern: `console\.(log|debug|info|warn|error)\s*\(`
- Skip files in `tests/`, `test/`, `__tests__/`, `*.test.*`, `*.spec.*`

#### Universal Rules (`universal_rules.py`)

**hardcoded-secret:**
- Patterns: `(api[_-]?key|secret|password|token)\s*[:=]\s*["'][a-zA-Z0-9]{16,}["']`
- Case insensitive
- Skip `.env.example`, `*.md`, `*.txt`

**todo-fixme:**
- Pattern: `#\s*(TODO|FIXME|HACK|XXX)\b` and `//\s*(TODO|FIXME|HACK|XXX)\b`

**god-module:**
- Count lines per file
- Report if > 800 lines
- Line number = 0 (file-level violation)

### Engine Flow (`engine.py`)

```
run_analysis(cwd):
  1. Discover files (reuse repo_map's skip logic)
  2. Group by language (.py / .js/.ts / other)
  3. For each Python file: parse AST, run python_rules
  4. For each JS/TS file: read content, run js_rules
  5. For all files: run universal_rules
  6. Run circular import detection (cross-file, Python only)
  7. Collect violations, sort by severity then file
  8. Save to cache (.llm-code/last_analysis.json)
  9. Return AnalysisResult
```

### File Discovery
Reuse the skip-directory logic from `repo_map.py` and `dump.py`:
- Skip: `.git`, `__pycache__`, `node_modules`, `.venv`, `venv`, `dist`, `build`
- Max files: 500 (configurable)

---

## Integration Points

| Existing Module | Relationship |
|----------------|-------------|
| `repo_map.py` | Shares file discovery logic (don't duplicate) |
| `auto_diagnose.py` (F6) | Complementary — LSP handles type errors, analysis handles semantic rules |
| `dump.py` (F4) | Shares skip-directory patterns |
| `status_bar.py` | No integration needed |

## Not in Scope

- LLM-based semantic analysis (TrueCourse does this, we don't need it — agent IS the LLM)
- Web UI / dependency graph visualization
- Cross-service flow tracing
- Database schema analysis
- Custom user-defined rules (future enhancement)
- Tree-sitter integration (regex is sufficient for JS/TS)

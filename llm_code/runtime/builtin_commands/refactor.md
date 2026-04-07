---
description: Intelligent refactor with codebase analysis, planning, and verification
argument_hint: "<refactoring-target> [--scope=<file|module|project>] [--strategy=<safe|aggressive>]"
---

# Intelligent Refactor

Performs intelligent, deterministic refactoring with full codebase awareness. Unlike blind search-and-replace, this command:

1. **Understands your intent** — analyzes what you actually want to achieve
2. **Maps the codebase** — builds a definitive codemap before touching anything
3. **Assesses risk** — evaluates test coverage and determines verification strategy
4. **Plans meticulously** — creates a detailed plan before any edits
5. **Executes precisely** — step-by-step refactoring with verification after every step
6. **Verifies constantly** — runs tests after each change to ensure zero regression

---

## Phase 0: Intent Gate (MANDATORY FIRST STEP)

Classify the request:

| Signal | Classification | Action |
|--------|----------------|--------|
| Specific file/symbol | Explicit | Proceed to codebase analysis |
| "Refactor X to Y" | Clear transformation | Proceed to codebase analysis |
| "Improve", "Clean up" | Open-ended | MUST ask: "What specific improvement?" |
| Ambiguous scope | Uncertain | MUST ask: "Which modules/files?" |
| Missing context | Incomplete | MUST ask: "What's the desired outcome?" |

Before proceeding, confirm:
- [ ] Target is clearly identified
- [ ] Desired outcome is understood
- [ ] Scope is defined (file/module/project)
- [ ] Success criteria can be articulated

Create a todo list for phases 1–6 immediately.

---

## Phase 1: Codebase Analysis (parallel exploration)

Fire 5 parallel explore tasks:
- Find all occurrences and definitions of the target
- Find all code that imports, uses, or depends on the target
- Find similar patterns in the codebase
- Find all test files related to the target
- Find architectural patterns and module organization around the target

While they run, use direct tools (grep, ast-grep, file reading) on the target itself.

---

## Phase 2: Build Codemap

Construct:

```
## CODEMAP: <target>

### Core Files (Direct Impact)
- path/to/file.py:L10-L50 — primary definition
- path/to/file2.py:L25  — key usage

### Dependency Graph
<target>
├── imports from: ...
├── imported by: ...
└── used by: ...

### Impact Zones
| Zone | Risk Level | Files Affected | Test Coverage |
|------|------------|----------------|---------------|
| Core | HIGH | 3 files | 85% covered |
| Consumers | MEDIUM | 8 files | 70% covered |
| Edge | LOW | 2 files | 50% covered |

### Refactoring Constraints
- MUST follow: <existing patterns>
- MUST NOT break: <critical dependencies>
- Safe to change: <isolated zones>
```

---

## Phase 3: Test Assessment

Detect test infrastructure (`pytest`, `bun test`, `go test`, etc.) and analyze coverage for the target.

| Coverage | Strategy |
|----------|----------|
| HIGH (>80%) | Run existing tests after each step |
| MEDIUM (50-80%) | Run tests + add safety assertions |
| LOW (<50%) | PAUSE: propose adding tests first |
| NONE | BLOCK: refuse aggressive refactoring |

If LOW or NONE, ASK the user before proceeding.

---

## Phase 4: Plan Generation

Create a detailed refactoring plan with:
1. Atomic refactoring steps
2. Each step independently verifiable
3. Order respecting dependencies
4. Exact files and line ranges per step
5. Rollback strategy per step
6. Commit checkpoints

Convert each step into a granular todo.

---

## Phase 5: Execute Refactoring

For EACH step:
1. Mark the step todo as in_progress
2. Read current file state, verify diagnostics baseline
3. Execute the change (preview first with dry-run when possible)
4. Verify: diagnostics clean, tests pass, type check clean
5. Mark step todo as completed

If verification fails: STOP, REVERT, DIAGNOSE, then fix or escalate. NEVER proceed with broken tests.

---

## Phase 6: Final Verification

Run the full test suite, type check, lint, and build. Confirm all changed files have clean diagnostics. No regressions.

---

## Critical Rules

NEVER: skip verification, proceed with failing tests, suppress type errors, delete tests to make them pass, commit broken code.

ALWAYS: understand before changing, preview before applying, verify after every change, follow existing patterns, keep todos updated, commit at logical checkpoints.

ABORT if: test coverage is zero, public API would break, scope unclear, 3 consecutive verification failures.

---

User request:
$ARGUMENTS

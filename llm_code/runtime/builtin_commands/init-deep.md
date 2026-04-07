---
description: Generate hierarchical AGENTS.md files for the project root and complexity-scored subdirectories
argument_hint: "[--create-new] [--max-depth=N]"
---

# /init-deep

Generate hierarchical AGENTS.md files. Root + complexity-scored subdirectories.

## Usage

```
/init-deep                      # Update mode: modify existing + create new where warranted
/init-deep --create-new         # Read existing → remove all → regenerate from scratch
/init-deep --max-depth=2        # Limit directory depth (default: 3)
```

## Workflow

1. **Discovery + Analysis** (concurrent)
   - Fire background explore agents immediately
   - Main session: bash structure scan + read existing AGENTS.md
2. **Score & Decide** — determine AGENTS.md locations from merged findings
3. **Generate** — root first, then subdirs in parallel
4. **Review** — deduplicate, trim, validate

Create a todo list immediately tracking these phases. Mark each in_progress → completed in real time.

---

## Phase 1: Discovery + Analysis (Concurrent)

### Fire background explore agents IMMEDIATELY

```
explore: Project structure — predict standard patterns for detected language, report deviations only
explore: Entry points — find main files, report non-standard organization
explore: Conventions — find config files (.eslintrc, pyproject.toml, .editorconfig), report project-specific rules
explore: Anti-patterns — find "DO NOT", "NEVER", "ALWAYS", "DEPRECATED" comments, list forbidden patterns
explore: Build/CI — find .github/workflows, Makefile, report non-standard patterns
explore: Test patterns — find test configs and structure, report unique conventions
```

### Dynamic agent spawning

After bash analysis, spawn ADDITIONAL explore agents based on project scale:

| Factor | Threshold | Additional Agents |
|--------|-----------|-------------------|
| Total files | >100 | +1 per 100 files |
| Total lines | >10k | +1 per 10k lines |
| Directory depth | ≥4 | +2 for deep exploration |
| Large files (>500 lines) | >10 | +1 for complexity hotspots |
| Monorepo | detected | +1 per package/workspace |
| Multiple languages | >1 | +1 per language |

### Main session: concurrent analysis

```bash
# Directory depth + file counts
find . -type d -not -path '*/\.*' -not -path '*/node_modules/*' -not -path '*/venv/*' | awk -F/ '{print NF-1}' | sort -n | uniq -c

# Files per directory (top 30)
find . -type f -not -path '*/\.*' -not -path '*/node_modules/*' | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -30

# Existing AGENTS.md / CLAUDE.md
find . -type f \( -name "AGENTS.md" -o -name "CLAUDE.md" \) -not -path '*/node_modules/*' 2>/dev/null
```

Read each existing AGENTS.md/CLAUDE.md and extract: key insights, conventions, anti-patterns.

If `--create-new`: read all existing FIRST (preserve context) → then delete all → regenerate.

---

## Phase 2: Scoring & Location Decision

| Factor | Weight | High Threshold |
|--------|--------|----------------|
| File count | 3x | >20 |
| Subdir count | 2x | >5 |
| Code ratio | 2x | >70% |
| Unique patterns | 1x | Has own config |
| Module boundary | 2x | Has index/__init__ |

| Score | Action |
|-------|--------|
| Root (.) | ALWAYS create |
| >15 | Create AGENTS.md |
| 8-15 | Create if distinct domain |
| <8 | Skip (parent covers) |

---

## Phase 3: Generate AGENTS.md

### Root AGENTS.md (full treatment)

```markdown
# PROJECT KNOWLEDGE BASE

**Generated:** {TIMESTAMP}
**Commit:** {SHORT_SHA}
**Branch:** {BRANCH}

## OVERVIEW
{1-2 sentences: what + core stack}

## STRUCTURE
{tree with non-obvious purposes only}

## WHERE TO LOOK
| Task | Location | Notes |

## CONVENTIONS
{ONLY deviations from standard}

## ANTI-PATTERNS (THIS PROJECT)
{Explicitly forbidden here}

## COMMANDS
{dev/test/build}

## NOTES
{Gotchas}
```

**Quality gates**: 50-150 lines, no generic advice, no obvious info.

### Subdirectory AGENTS.md (parallel)

For each location: 30-80 lines max, NEVER repeat parent content. Sections: OVERVIEW (1 line), STRUCTURE (if >5 subdirs), WHERE TO LOOK, CONVENTIONS (if different), ANTI-PATTERNS.

---

## Phase 4: Review & Deduplicate

For each generated file: remove generic advice, remove parent duplicates, trim to size limits, verify telegraphic style.

---

## Anti-Patterns

- Static agent count: MUST vary agents based on project size/depth
- Sequential execution: MUST parallel (explore + bash concurrent)
- Ignoring existing: ALWAYS read existing first, even with --create-new
- Over-documenting: not every dir needs AGENTS.md
- Redundancy: child never repeats parent
- Generic content: remove anything that applies to ALL projects
- Verbose style: telegraphic or die

$ARGUMENTS

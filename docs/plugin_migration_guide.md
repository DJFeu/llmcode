# Plugin Migration Guide — v1.x → v2.2

llmcode v2.2 removes the transitional shims that let v1-style plugins
keep running after the v12 engine landed. This guide walks plugin
authors through the automated migration CLI (`llmcode migrate v12`)
and documents the manual fallbacks for patterns the codemod cannot
handle automatically.

---

## Overview

The codemod is an `libcst`-based source rewriter. It operates on a
plugin source tree in place (or in `--dry-run` mode prints a unified
diff), preserving comments and formatting. Four rewriters ship in
v2.2; each handles one migration shape.

| Rewriter | Legacy shape | v2.2 shape |
|----------|--------------|------------|
| `tool_pipeline_subclass` | `class X(ToolExecutionPipeline): …` | `@component` class + `register(pipeline)` helper |
| `prompt_mode_import` | `from llm_code.runtime.prompts.mode import beast` | `PromptBuilder(template_path="modes/beast.j2")` |
| `prompt_format_call` | `prompt.format(a=1, b=2)` | `PromptBuilder(template=prompt).run(a=1, b=2)["prompt"]` |
| `pyproject_constraint` | `llmcode>=1.0` in `pyproject.toml` | `llmcode>=2.0,<3.0` |

Unsupported shapes (metaprogramming on `self.__class__`, private-method
reach-through, positional-only `.format()` calls, etc.) are reported
with `file:line` diagnostics and left unchanged; manual migration is
required.

---

## Invocation

```bash
# Dry-run: print the unified diff, write nothing
llmcode migrate v12 ./path/to/plugin --dry-run

# Apply: write changes in place
llmcode migrate v12 ./path/to/plugin

# Subset of rewriters
llmcode migrate v12 ./path/to/plugin --rewriters=tool_pipeline_subclass,prompt_mode_import

# Structured diagnostics report
llmcode migrate v12 ./path/to/plugin --report migration-report.json
```

Exit codes:

- `0` — success (with or without changes)
- `1` — unsupported patterns encountered (report printed)
- `2` — runtime error (stack trace printed)

---

## Rewriter catalogue

### 1. `tool_pipeline_subclass`

**Before:**

```python
from llm_code.runtime.tool_pipeline import ToolExecutionPipeline


class AuditingPipeline(ToolExecutionPipeline):
    """Pre-execution audit log."""

    def pre_execute(self, call):
        logger.info("tool=%s args=%s", call.name, call.args)
        return super().pre_execute(call)

    def post_process(self, result):
        logger.info("result is_error=%s", result.is_error)
        return super().post_process(result)
```

**After:**

```python
from llm_code.engine import component, Pipeline


@component
class AuditingPipeline:
    """Pre-execution audit log."""

    def run(self, call):
        logger.info("tool=%s args=%s", call.name, call.args)
        result = self._execute(call)
        logger.info("result is_error=%s", result.is_error)
        return {"result": result}


def register(pipeline: Pipeline) -> None:
    pipeline.add_component("auditing", AuditingPipeline())
```

Plugin hosts call `register(pipeline)` at startup instead of
instantiating the subclass directly. See
[`docs/engine/components.md`](./engine/components.md) for the full
Component decorator reference.

**Unsupported shapes** flagged with `file:line`:

- Metaprogramming on `self.__class__` (common in dynamic plugin
  registries). Rewrite by pulling the metaprogrammed attributes out
  into explicit helper functions.
- Reaching into private methods (`self._validate_input(...)` instead
  of `super().validate_input(...)`). The legacy private methods do
  not survive to v2.2 — replicate the needed logic inside your
  Component.

### 2. `prompt_mode_import`

**Before:**

```python
from llm_code.runtime.prompts.mode import beast as beast_prompt

text = beast_prompt.format(model="gpt-4")
```

**After:**

```python
from llm_code.engine.prompt_builder import PromptBuilder

text = PromptBuilder(template_path="modes/beast.j2").run(model="gpt-4")["prompt"]
```

The templates are shipped in `llm_code/engine/prompts/modes/`. Aliased
imports (`import X as Y`) are preserved; the `Y` symbol continues to
resolve to a rendered template string. Direct-module imports
(`import llm_code.runtime.prompts.mode.beast`) are flagged as
unsupported; rewrite by hand.

### 3. `prompt_format_call`

**Before:**

```python
from llm_code.runtime.prompts import beast

rendered = beast.format(user="Alice", task="review the diff")
```

**After:**

```python
from llm_code.engine.prompt_builder import PromptBuilder

rendered = PromptBuilder(template=beast).run(user="Alice", task="review the diff")["prompt"]
```

Positional `.format(positional)` calls are unsupported — rewrite them
to keyword form before running the codemod:

```python
# Before (unsupported)
rendered = prompt.format("Alice", "review the diff")

# After (rewriter-friendly)
rendered = prompt.format(user="Alice", task="review the diff")
```

Mixed-use cases (variable used with `.format()` *and* as a plain string
elsewhere) are supported — the original variable is preserved so
non-format uses continue to compile.

### 4. `pyproject_constraint`

**Before:**

```toml
[project.dependencies]
llmcode = ">=1.0"
```

**After:**

```toml
[project.dependencies]
llmcode = ">=2.0,<3.0"
```

Covers PEP 621 `[project.dependencies]`, Poetry
`[tool.poetry.dependencies]`, and Hatch `[dependency-groups]` layouts.
Unrelated dependency lines are untouched.

---

## Manual migration fallback

For any shape the codemod flags as unsupported:

1. Read the diagnostic. It includes the file path, line number, and
   a pointer to the rewriter that flagged the pattern.
2. Find the target v2.2 shape in the catalogue above (or in
   `docs/engine/components.md` for the full Component API).
3. Rewrite by hand, keeping behaviour identical.
4. Re-run the codemod to confirm no other offending patterns remain:

   ```bash
   llmcode migrate v12 ./path/to/plugin --dry-run
   ```

   A clean run returns exit code 0 with no diff.

---

## CI integration

The codemod is idempotent — running it a second time on already-migrated
source is a no-op. A reasonable CI guard is:

```yaml
# .github/workflows/plugin-ci.yml
- name: Check plugin is on v2.2 layout
  run: llmcode migrate v12 . --dry-run
  # non-zero exit on unsupported patterns fails the PR
```

---

## Getting help

- Open an issue at https://github.com/DJFeu/llmcode/issues with the
  codemod's JSON report attached.
- Tag `v12-migration` for faster triage during the v2.2 release window.

# Prompt Template Author Guide

> **Status:** v12 M1 — Jinja2 `PromptBuilder` is the only prompt
> assembly path. Every mode, section, model banner, and reminder lives
> under `llm_code/engine/prompts/**/*.j2`.

## Section table of contents

1. Template tree layout
2. Adding a new mode
3. Adding a new section
4. Adding a new reminder
5. `{% include %}` vs `{% extends %}`
6. Variable naming conventions
7. Escaping user-controlled text
8. `PromptBuilder` API
9. Testing requirements

## 1. Template tree layout

```
llm_code/engine/prompts/
  base.j2                       # root skeleton — blocks declared here
  models/<name>.j2              # per-model banners (anthropic, gpt, qwen, ...)
  modes/<name>.j2               # mode-specific system reminders (plan, max_steps, build_switch, ...)
  sections/<name>/<variant>.j2  # reusable sections (sections/memory/default.j2, ...)
  reminders/<name>.j2           # one-shot reminders injected as system msgs
```

Each tier has exactly one responsibility:

- **base.j2** defines the `{% block %}`s every other file may override:
  `capability_intro`, `tools`, `memory_context`, `permission_hint`,
  `mode_specific`, `reminders`.
- **models/** banners replace `capability_intro` + `tools` for one
  model family. Keep banner files under 80 lines — one line per
  non-obvious policy.
- **modes/** emit the full `<system-reminder>…</system-reminder>`
  payload. Match the existing tone in `modes/plan.j2` (see the
  `CRITICAL —` prefix convention).
- **sections/** render snippets slot into blocks at assemble time.
  Sub-folder = semantic family (memory, tools, permissions).
- **reminders/** are injected by the Agent loop *between* turns
  (not at system-prompt build time) — e.g. max-steps warning.

## 2. Adding a new mode

1. Create `llm_code/engine/prompts/modes/<name>.j2`. Wrap the payload
   in `<system-reminder>` if it should be treated as authoritative
   guidance — every existing mode uses that.
2. Add a constant to the mode enum in `llm_code/engine/state.py`
   (`Mode.YOUR_MODE`) and register it in the runtime routing table.
3. Write a parity test under
   `tests/test_engine/test_prompt_templates.py`:

```python
from llm_code.engine.prompt_builder import render_template_file

def test_your_mode_renders():
    out = render_template_file("modes/your_mode.j2", plan_file_section="")
    assert "CRITICAL" in out
    assert out.count("{{") == 0          # no un-rendered placeholders
```

## 3. Adding a new section

Sections are small (10–40 line) reusable fragments. Put them in
`sections/<family>/<variant>.j2` — grouping by family means callers
can switch implementations at wire time (see
`MemoryConfig.context_template = "default" | "compact"`).

```jinja
{# sections/memory/compact.j2 #}
{% set count = entry_count | default(entries | length) %}
{% if count > 0 %}
Memory: {% for e in entries %}{{ e.text[:120] | e }}{% if not loop.last %}; {% endif %}{% endfor %}
{% endif %}
```

Section variables should be documented at the top of the file in a
Jinja comment block (`{# … #}`) — the engine parser strips them and
the section author + downstream readers share a single source of
truth.

## 4. Adding a new reminder

Reminders are injected by the Agent loop *after* the system prompt.
Keep them short (1–3 sentences) and imperative. Expose a Python hook
on the relevant `ExitCondition` or `DegradedModePolicy` so the
Agent knows when to render it — e.g. `MaxStepsReached.warning_reminder`
returns `None` most of the time, a string when `iteration ==
cap - warning_offset`.

## 5. `{% include %}` vs `{% extends %}`

| Use | When |
|------|------|
| `{% extends "base.j2" %}` | Your template replaces one or more blocks declared in `base.j2`. Only one `extends` per file. |
| `{% include "sections/foo.j2" %}` | Your template pulls a reusable fragment verbatim into the current render. Include statements are cheap — re-use aggressively. |

Rule of thumb: **modes extend, sections include**. Never call
`include` from inside a `for` loop — Jinja compiles templates once
per file, not once per iteration, but the inclusion cost compounds
fast on long inputs.

## 6. Variable naming conventions

- Snake case always (`plan_file_section`, not `planFileSection`).
- Collections plural (`entries`, `reminders`). Scalars singular.
- Flags are `has_<thing>` or `is_<thing>`.
- Counts are `<thing>_count` — matches the existing
  `entry_count` in `sections/memory/default.j2`.
- Never reference a variable that is not in `required_variables`
  unless it has a `| default(...)` filter.

## 7. Escaping user-controlled text

`PromptBuilder` sets `autoescape=False` because prompts are plain
text, not HTML. When a variable carries user input (tool names,
memory text, file paths, etc.), **pipe it through `| e`** so any
stray `{{` `}}` `{%` `%}` can't be re-parsed downstream:

```jinja
{{ (entry.source_tool or 'unknown') | e }}: {{ entry.text[:280] | e }}
```

The two existing memory templates
(`sections/memory/default.j2`, `sections/memory/compact.j2`) are
the canonical examples.

## 8. `PromptBuilder` API

```python
from llm_code.engine.prompt_builder import PromptBuilder, render_template_file

# Inline template
builder = PromptBuilder(template="Hello {{ name }}", required_variables=("name",))
print(builder.run(name="world")["prompt"])

# File-backed template
builder = PromptBuilder(template_path="modes/plan.j2")
print(builder.run(plan_file_section="")["prompt"])

# Shortcut — one call, no caller-held instance
text = render_template_file("sections/memory/default.j2",
                            entries=my_entries, entry_count=len(my_entries))
```

Key behaviours:

- Pass **exactly one** of `template` or `template_path`.
- `required_variables` is a whitelist — missing keys raise
  `ValueError` before Jinja even starts rendering.
- `StrictUndefined` is always active: referencing a variable that
  isn't passed raises `jinja2.UndefinedError`. This catches typo
  regressions in CI.
- `run()` returns `{"prompt": <str>}` — the dict shape lets the
  `PromptAssemblerComponent` wrap it in a socket without signature
  churn.
- `builder.declared_variables` gives you the set of names the
  template references — useful for `llmcode` doctor checks that
  validate every known caller supplies every declared variable.

## 9. Testing requirements

Each new template must have at least:

1. **Happy path** — renders with canonical inputs, contains the
   expected literal phrase (grep-style assertion).
2. **Escape check** — if the template renders user input, assert
   that a payload containing `{{ evil }}` is rendered literally
   (not re-parsed).
3. **Empty-input branch** — if the template has a `{% if %}` gate
   (like memory sections dropping themselves on empty entries),
   assert the output is empty string.

Suggested file: `tests/test_engine/test_prompt_templates.py`.
Coverage floor for M1 is 95% line coverage on `prompt_builder.py`
plus 100% branch coverage on every new template (enforced by the
CI parity job).

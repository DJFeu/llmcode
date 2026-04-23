# Model Profile Author Guide

> **Status:** v13 Phase A — profile schema extension (backward
> compatible). Phase A ships new ``[prompt]`` authoring fields on the
> profile TOML; the ``select_intro_prompt`` shim still honours every
> pre-v13 model. Phase B migrates the shipped profiles. Phase C deletes
> the legacy if-ladder. See the companion plans under
> ``docs/superpowers/plans/2026-04-24-llm-code-v13-*``.

## Section table of contents

1. What a model profile is
2. Where profiles live
3. Required and optional fields
4. The new ``[prompt]`` section (v13)
5. Walkthrough — adding a new model family (FooChat-13B)
6. Troubleshooting — "my model is not picking up the right prompt"
7. Where to look next

## 1. What a model profile is

A **model profile** is a TOML file that tells llmcode how to drive a
specific LLM. The same model id on different providers often behaves
differently (temperature defaults, reasoning channel naming, whether
native function calling actually works, whether the server injects a
``<think>`` prefix, which system-prompt flavour produces the best
behaviour). A profile puts all of that in one declarative place so
the core engine stays provider-agnostic.

After v13 the profile is the single source of truth for "what does
this model need?" — including its intro prompt, its tool-call parser
variants, and its streaming quirks. Adding a new model family means
writing a TOML file; it must not mean patching a ``if "foo" in
model_id`` branch in the core.

Profiles are parsed into the frozen dataclass
:class:`llm_code.runtime.model_profile.ModelProfile`. Every field has
a safe default so you only need to declare what differs from defaults.

## 2. Where profiles live

| Location | Loader | When loaded |
|---|---|---|
| ``examples/model_profiles/*.toml`` | ``_load_builtin_profiles`` (v13) / ``ProfileRegistry._load_user_profiles`` (pre-v13) | Lazy — first call to the deprecated ``select_intro_prompt`` shim. Packaged with the repo. |
| ``~/.llmcode/model_profiles/*.toml`` | ``ProfileRegistry`` (pre-v13 lookup by filename) | Runtime — discovered when the registry is constructed. Host-local overrides. |

To activate a new profile as a user, drop it in
``~/.llmcode/model_profiles/<model_id>.toml`` and either restart
llmcode or let the registry hot-reload (the directory's mtime is
checked on every ``get_profile`` call).

## 3. Required and optional fields

None of the fields are strictly required — a profile with just
``name = "..."`` parses and registers. The fields below are the ones
real profiles typically set. See the ``ModelProfile`` dataclass in
``llm_code/runtime/model_profile.py`` for the complete list.

### Provider / capability

```toml
[provider]
type = "openai-compat"       # or "anthropic"
native_tools = true          # set false when function calling is flaky
supports_reasoning = true
supports_images = false
force_xml_tools = false      # skip native tool attempt, go XML directly
```

### Streaming / thinking

```toml
[streaming]
implicit_thinking = false    # vLLM-style servers that inject <think>
reasoning_field = "reasoning_content"  # or "reasoning" (OpenAI o-series)

[thinking]
thinking_extra_body_format = "chat_template_kwargs"  # or "anthropic_native"
default_thinking_budget = 10000
```

### Sampling, pricing, limits, deployment

```toml
[sampling]
default_temperature = 0.55
reasoning_effort = "medium"  # "low" | "medium" | "high" | "max"

[pricing]
price_input = 3.00           # per 1M tokens
price_output = 15.00

[limits]
max_output_tokens = 16384
context_window = 200000

[deployment]
is_local = true              # enables unlimited token upgrades
```

## 4. The new ``[prompt]`` section (v13)

The ``[prompt]`` section is how a profile attaches itself to a set of
model ids **and** declares which intro template to render:

```toml
[prompt]
# Path to the Jinja2 template under llm_code/engine/prompts/. The
# short form ("glm") and the full path ("models/glm.j2") are both
# accepted so existing prompts in engine/prompts/models/<name>.j2
# keep working unchanged.
template = "models/glm.j2"

# Lowercase substrings. The first profile whose match list contains
# a substring of the user's model id is picked. Order matters: more
# specific tokens should come before generic ones within one profile.
match = ["glm", "zhipu"]
```

``match`` tokens must be globally unique — registering a second
profile that claims the same token raises ``ProfileMatchCollision``
at load time so silent shadowing is impossible. If your profile
needs to catch an existing claimant (e.g. you want to override the
built-in ``glm`` prompt in your user profile), register your profile
**before** the built-ins load or pass ``check_collision=False``
explicitly.

### What happens in Phase A

Phase A ships the schema and loader, but it does not migrate any of
the shipped profiles. The built-in TOMLs under ``examples/
model_profiles/`` still omit the ``[prompt]`` section, so every
existing model continues to route through the historical if-ladder
in ``_legacy_select_intro_prompt``. The deprecated shim
``select_intro_prompt`` emits a ``DeprecationWarning`` on every
call but preserves byte-level output. Phase B migrates the TOMLs;
Phase C deletes the ladder.

## 5. Walkthrough — adding a new model family (FooChat-13B)

Suppose FooChat-13B is a self-hosted reasoning model that emits its
chain-of-thought to a ``thinking_trace`` field, needs XML-tools
because native function calling is flaky, and prefers the ``qwen.j2``
prompt because the style matches closely.

**Step 1.** Write the TOML. Save it as
``~/.llmcode/model_profiles/foochat-13b.toml`` (or under
``examples/model_profiles/`` if you are contributing to llmcode
core):

```toml
name = "FooChat-13B (OSS)"

[provider]
type = "openai-compat"
native_tools = false
supports_reasoning = true
force_xml_tools = true

[streaming]
implicit_thinking = true
reasoning_field = "thinking_trace"

[thinking]
thinking_extra_body_format = "chat_template_kwargs"
default_thinking_budget = 8192

[sampling]
default_temperature = 0.5
reasoning_effort = "medium"

[deployment]
is_local = true

[limits]
max_output_tokens = 8192
context_window = 131072

[prompt]
template = "models/qwen.j2"
match = ["foochat"]
```

**Step 2.** Point ``~/.llmcode/config.json`` at the model:

```json
{ "model": "foochat-13b-q4_0" }
```

**Step 3.** Restart llmcode. On the first run the profile registry
loads the TOML, ``resolve_profile_for_model("foochat-13b-q4_0")``
matches the ``"foochat"`` substring, and
``load_intro_prompt(profile)`` renders the ``qwen.j2`` template.
Streaming, tool-call, thinking-budget, and context-window settings
all come from the profile — zero code changes.

**Step 4.** (Optional) Contribute upstream. Copy the file to
``examples/model_profiles/foochat-13b.toml`` in a PR against the
llmcode repository. The profile registry's
``_load_builtin_profiles`` sweep picks it up without further edits.

## 6. Troubleshooting

**"My profile is never selected."**
Verify ``prompt_match`` is lowercase and is a substring of the
model id you pass to llmcode. The resolver lowercases the id but
not the tokens; the TOML loader normalises tokens to lowercase.
Print the registry to debug:

```python
from llm_code.runtime import profile_registry as pr
pr._ensure_builtin_profiles_loaded()
for p in pr._PROFILES:
    print(p.name, p.prompt_match)
```

**"Two profiles fight for the same match token."**
``register_profile`` raises
``llm_code.runtime.profile_registry.ProfileMatchCollision`` with the
colliding token + both profile names. Fix by narrowing one of the
tokens (e.g. ``"glm-5"`` instead of ``"glm"``), or by loading your
profile first with ``check_collision=False``.

**"I migrated my profile but ``select_intro_prompt`` still returns
the old text."**
Phase A's shim only switches to the profile path when
``profile.prompt_template`` is non-empty. Double-check the
``[prompt]`` section parsed correctly by reading it back:

```python
profile = pr.resolve_profile_for_model("your-model-id")
print(profile.prompt_template, profile.prompt_match)
```

**"Template file not found — llmcode returned a generic fallback."**
``load_intro_prompt`` silently falls back to an inline safe default
when the configured ``.j2`` file is missing. Verify the path:

```bash
ls llm_code/engine/prompts/models/   # compare to profile.prompt_template
```

## 7. Where to look next

- ``docs/engine/prompt_template_author_guide.md`` — how the Jinja2
  templates under ``engine/prompts/`` are assembled.
- ``docs/superpowers/specs/2026-04-24-llm-code-v13-profile-driven-adapters-design.md``
  — full v13 design (Phase A / B / C roadmap).
- ``llm_code/runtime/model_profile.py`` — ``ModelProfile`` dataclass +
  ``ProfileRegistry`` (per-model-id lookup by filename).
- ``llm_code/runtime/profile_registry.py`` — v13 match-driven
  resolver (``resolve_profile_for_model``).
- ``tests/test_runtime/test_profile_registry.py`` +
  ``tests/test_runtime/test_prompt_loader.py`` — example usage and
  expected behaviour.

---

*Parser variants (``[parser]`` section) and streaming hints
(``[parser_hints]`` section) are authored the same way — covered in
Plan #2's author guide once Phase A of that plan lands.*

# Model Profile Author Guide

> **Status:** current profile-driven system. Built-in TOMLs declare their
> prompt, parser, provider, and model-behaviour settings; user TOMLs can
> override or extend them from ``~/.llmcode/model_profiles``.

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
| ``llm_code/_builtins/profiles/*.toml`` | ``ProfileRegistry`` built-ins / ``llmcode profiles`` CLI | Packaged in the wheel. These are the bundled defaults. |
| ``~/.llmcode/model_profiles/*.toml`` | ``ProfileRegistry`` user override loader | Runtime — discovered when the registry is constructed. Host-local overrides. |

To activate a new profile as a user, drop it in
``~/.llmcode/model_profiles/<model_id>.toml`` and either restart
llmcode or let the registry hot-reload (the directory's mtime is
checked on every ``get_profile`` call). The filename is still a valid
profile key, but runtime matching also honours ``[prompt].match`` aliases,
so ordered filenames such as ``41-gemma4.toml`` can cleanly register
``match = ["gemma4", "gemma-4"]`` and work with ``llmcode --model gemma4``.

Use ``llmcode profiles validate <model_id>`` or
``llmcode profiles validate --builtins`` to catch TOML syntax errors,
unknown providers, missing prompt templates, and unknown parser variants.

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

### Tool consumption (v14)

The ``[tool_consumption]`` section gates three optional runtime
mechanisms that paper over a class of model-level
instruction-following weaknesses where a model calls a tool, receives
data, and then writes a ``content`` response that contradicts the
tool result. Each mechanism is independently enabled per profile.

```toml
[tool_consumption]
# Mechanism A — append a synthetic <system-reminder> user message
# after each tool result, naming the tool just used. Cheapest of the
# three; ~40 tokens per tool call. Default ON so every model gets the
# protection unless the profile explicitly opts out.
reminder_after_each_call = true

# Mechanism B — drop reasoning_content / reasoning fields from prior
# assistant messages on the outbound request. Trades multi-turn
# reasoning continuity for grounded single-turn responses. Recommended
# for separate-reasoning-channel models that bleed denials across
# turns (GLM-5.1, DeepSeek-R1).
strip_prior_reasoning = false

# Mechanism C — after a turn's content streams, scan for denial
# keywords; if a tool was called this turn AND a denial pattern
# matches, re-invoke the provider once with an injected continuation
# reminder. Capped at 1 retry. Buffers streaming for retry-eligible
# turns (TTFT trade-off). Costs +1 provider call per denial-matched
# turn. Adopter-only opt-in for self-hosted local models known to
# have weak tool-result consumption.
retry_on_denial = false
```

See ``docs/superpowers/specs/2026-04-27-llm-code-v14-tool-consumption-compat-design.md``
for the design rationale (why prompt-level fixes alone are insufficient
against models like GLM-5.1) and per-mechanism trade-offs.

#### When to enable ``retry_on_denial``

The denial-detection retry is the most invasive of the three v14
mechanisms — it costs an extra provider call when triggered, buffers
streaming output (sacrificing time-to-first-token), and can produce
a ``denial_retry_failed`` warning if the model digs in on a denial
pattern across both calls. Enable when:

- **Symptom present.** The model calls a tool, receives data, and
  writes a denial in ``content`` ("I don't have access to news APIs"
  after just calling ``web_search``). Run the model against a
  realtime query (e.g. ``顯示今日熱門新聞三則``) and inspect the
  rendered output — if it contains a denial keyword instead of the
  tool result, this profile is a candidate.
- **Cost.** +1 provider call per denial-matched turn. Observed rate
  on GLM-5.1 news/realtime queries: ~30%+. Frontier hosted models
  (Claude, GPT-4o, Gemini Pro) typically observe <1% — leave them
  with the flag off.
- **UX.** Streaming becomes buffered for retry-eligible turns. The
  user sees the response in one shot instead of token-by-token.
  Acceptable for back-end / batch usage; potentially jarring for
  interactive REPL users. Streaming purists turn the flag off.
- **Recommendation.** Enable for self-hosted local models known to
  have weak tool-result consumption (GLM-5.1, possibly Llama-3.3
  fine-tunes). Leave disabled for hosted frontier models. Enable
  Mechanism A (default ON) and B (opt-in) first; only enable C if
  A+B alone don't fix the failure mode.

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

### Current prompt routing

Built-in and user TOMLs can declare ``[prompt]``. ``template`` accepts a
short name such as ``"qwen"`` or a path-like value such as
``"models/qwen.j2"``; both resolve under
``llm_code/engine/prompts/models``. Prompt routing is profile-driven, so
adding a new model family should not require adding model-name checks to
runtime prompt code.

## 5. Walkthrough — adding a new model family (FooChat-13B)

Suppose FooChat-13B is a self-hosted reasoning model that emits its
chain-of-thought to a ``thinking_trace`` field, needs XML-tools
because native function calling is flaky, and prefers the ``qwen.j2``
prompt because the style matches closely.

**Step 1.** Write the TOML. Save it as
``~/.llmcode/model_profiles/foochat-13b.toml`` (or under
``llm_code/_builtins/profiles/NN-foochat-13b.toml`` if you are
contributing to llmcode core):

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
``llm_code/_builtins/profiles/NN-foochat-13b.toml`` in a PR against the
llmcode repository, where ``NN`` preserves the bundled display order.
Run ``llmcode profiles validate --builtins`` before submitting.

## 6. Troubleshooting

**"My profile is never selected."**
For user profiles, the TOML filename stem is the primary match key.
Use a stem that is either the exact model id or a prefix of the model id,
for example ``foochat-13b.toml`` for ``foochat-13b-q4_0``. Then inspect
the active resolution:

```bash
llmcode doctor
llmcode config explain
```

**"My prompt template is not being used."**
Double-check the ``[prompt]`` section parsed correctly and that the
template exists:

```python
from llm_code.runtime.model_profile import get_profile
profile = get_profile("your-model-id")
print(profile.prompt_template)
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
- ``llm_code/runtime/model_profile.py`` — ``ModelProfile`` dataclass +
  ``ProfileRegistry`` (per-model-id lookup by filename).
- ``tests/test_runtime/test_profile_registry.py`` +
  ``tests/test_runtime/test_prompt_loader.py`` — example usage and
  expected behaviour.

---

*Parser variants (``[parser]`` section) and streaming hints
(``[parser_hints]`` section) are authored the same way and are checked
by ``llmcode profiles validate``.*

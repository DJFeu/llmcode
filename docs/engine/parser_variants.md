# Parser Variants — v13 Phase A

llm-code's tool-call parser is a pluggable registry of named
variants. Each variant knows how to detect and parse one on-wire
format that an LLM may emit as a tool call. A profile TOML picks
which variants are enabled, and in what order.

This page is the reference for:

- The six built-in variants
- What the `match` / `parse` / `requires_standard_close_when` fields
  mean
- How to write a plugin variant for a new model format

---

## Built-in variants

All built-ins live in `llm_code/tools/parser_variants.py` and are
registered at module import. They extract the leaf parse logic from
`llm_code/tools/parsing.py` without changing semantics.

### `json_payload` — llm-code's original protocol

On-wire shape:

```
<tool_call>{"tool": "NAME", "args": {...}}</tool_call>
```

- **Match:** body (after left-strip) starts with `{` AND contains
  `"tool"`.
- **Parse:** JSON decode, require `tool` key, accept optional `args`
  dict (defaults to `{}` if missing).
- **Requires standard close when:** never.

### `hermes_function` — Qwen3 / NousHermes full form

On-wire shape:

```
<tool_call>
<function=NAME>
<parameter=KEY>VALUE</parameter>
...
</function>
</tool_call>
```

- **Match:** body contains `<function=`.
- **Parse:** extract name from `<function=NAME>`, then either
  collect `<parameter=>` blocks OR fall back to a JSON object body.
  Accepts the `args` / `arguments` wrapper pattern for the latter.
- **Requires standard close when:** never.

### `hermes_truncated` — vLLM template-truncated Hermes

Some chat templates inject `<tool_call>\n<function=` as the
assistant prompt prefix, so the model only emits `NAME>...</function>`.
A companion shape drops the `>` separator entirely:
`NAME{"args": {...}}`.

- **Match:** body at start matches `^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*(?:>|\{)`.
- **Parse:** same as `hermes_function`; the internal truncated-form
  regex handles the bare-identifier prefix.
- **Requires standard close when:** never.

### `harmony_kv` — Harmony / GLM key-value body (variant 7)

On-wire shape:

```
<tool_call>
NAME
<arg_key>KEY</arg_key>
<arg_value>VALUE</arg_value>
...
</tool_call>
```

Values that round-trip as JSON scalars are decoded (`"5"` → `5`,
`"true"` → `True`).

- **Match:** body contains `<arg_key>`.
- **Parse:** extract name from the first non-empty line of the
  preamble, collect all `<arg_key>/<arg_value>` pairs.
- **Requires standard close when:** `("<arg_key>",)` — the stream
  parser MUST wait for `</tool_call>` when this marker appears in
  the buffer. Without this guard, the variant body's interior
  `</arg_value>` tags would terminate the block early.

### `glm_brace` — GLM-5.1 `NAME}{JSON}</arg_value>` (variant 6)

On-wire shape:

```
<tool_call>NAME}{"query":"...","max_results":5}</arg_value>
```

Sibling calls are separated by U+2192 (`→`) instead of being
wrapped in separate `<tool_call>` blocks.

- **Match:** body at start matches `^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\s*\{`.
- **Parse:** extract name, parse the JSON body, reject non-dict
  shapes and reserved names.
- **Requires standard close when:** never.
- **Stream parser hint:** profiles that enable this variant should
  set `custom_close_tags = ["</arg_value>"]` and
  `call_separator_chars = "→ \t\r\n"` in their
  `[streaming.parser_hints]` section so the streaming parser
  terminates blocks on `</arg_value>` and consumes the `→`
  separator between chained calls.

### `bare_name_tag` — wrapper-less `<NAME>JSON</NAME>` (variant 5)

On-wire shape:

```
<web_search>{"query": "x", "max_results": 3}</web_search>
```

The closing tag may NOT match the opening tag — observed in the
wild: `<web_search>{"q":"x"}</search>`.

- **Match:** body contains `<NAME>{...}</NAME>` anywhere.
- **Parse:** require dict JSON body, unwrap `args`/`arguments`
  nesting, reject reserved names (`tool_call`, `think`, `function`,
  `parameter`), optionally filter by `known_tool_names` at the
  multi-call fallback level.
- **Requires standard close when:** never.

---

## `DEFAULT_VARIANT_ORDER`

When a profile doesn't set `parser_variants`, the registry uses:

```python
DEFAULT_VARIANT_ORDER = (
    "json_payload",
    "hermes_function",
    "hermes_truncated",
    "harmony_kv",
    "glm_brace",
    "bare_name_tag",
)
```

This mirrors the pre-v13 sequence in `parsing._parse_xml` exactly.
Parity is the gate: switching from `profile=None` to an explicit
profile with the same variant tuple must produce byte-identical
output on the same input. See `test_parser_variant_registry.py`
for the parity tests.

---

## Profile TOML example

```toml
name = "GLM-5.1"

[provider]
type = "openai-compat"

[parser]
# Empty = use DEFAULT_VARIANT_ORDER. Listing variants here picks
# an explicit subset + order.
variants = [
    "json_payload",
    "hermes_function",
    "harmony_kv",
    "glm_brace",
    "bare_name_tag",
]

[parser_hints]
# GLM-5.1 variant 6 uses </arg_value> as close and U+2192 as the
# call separator.
custom_close_tags = ["</arg_value>"]
call_separator_chars = "\u2192 \t\r\n"
```

A profile for a simpler provider (e.g. Claude-compat) can omit both
sections entirely — the resolver falls back to defaults.

---

## Writing a plugin variant

A plugin variant is a standalone Python module that defines a
`ParserVariant` at module level. The profile lists it via a
dotted path like `my_pkg.my_mod:MyVariant`. At first use, llm-code
`importlib.import_module`s the module, `getattr`s the attribute,
validates it's a `ParserVariant`, and registers it by its `name`.

### Walkthrough: a hypothetical CustomPipe variant

Suppose a new model emits tool calls as `<tool_call>NAME|KEY1=VAL1|KEY2=VAL2</tool_call>`.

1. Create `my_pkg/custom_pipe.py`:

   ```python
   from __future__ import annotations

   import re
   import uuid

   from llm_code.tools.parser_variants import ParserVariant
   from llm_code.tools.parsing import ParsedToolCall

   _PIPE_RE = re.compile(
       r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|(.*))?$",
       re.DOTALL,
   )


   def _match(raw: str) -> bool:
       # Cheap peek — body starts with an identifier, contains `|`.
       return "|" in raw and _PIPE_RE.match(raw) is not None


   def _parse(raw: str) -> ParsedToolCall | None:
       m = _PIPE_RE.match(raw)
       if m is None:
           return None
       name = m.group(1)
       body = m.group(2) or ""
       args: dict[str, str] = {}
       for part in body.split("|"):
           if "=" not in part:
               continue
           key, _, value = part.partition("=")
           args[key.strip()] = value.strip()
       if not args and not name:
           return None
       return ParsedToolCall(
           id=str(uuid.uuid4()),
           name=name,
           args=args,
           source="xml_tag",
       )


   CustomPipe = ParserVariant(
       name="custom_pipe",
       match=_match,
       parse=_parse,
   )
   ```

2. In your profile TOML:

   ```toml
   [parser]
   variants = [
       "json_payload",
       "my_pkg.custom_pipe:CustomPipe",   # plugin
       "hermes_function",
   ]
   ```

3. Make sure `my_pkg` is importable (on `sys.path`, installed via
   pip, or in the project's working directory).

4. On first call, llm-code:
   - Sees `"my_pkg.custom_pipe:CustomPipe"` contains `:`, so
     triggers `load_plugin_variant`.
   - Imports `my_pkg.custom_pipe`, reads `CustomPipe`, verifies
     it's a `ParserVariant` instance.
   - Registers it under its declared `name` (`"custom_pipe"`).
   - Uses it in the per-`<tool_call>`-block loop.

### Plugin security notes

The loader only accepts dotted paths resolvable via `sys.path`. It
does NOT execute arbitrary strings, fetch URLs, or eval expressions.
The module's top-level code runs at import time (standard Python
semantics), so plugins are trusted code — audit before enabling.

### Stream parser hints for plugins

If your variant needs non-standard close tags (like GLM variant 6's
`</arg_value>`), also set `[parser_hints] custom_close_tags` in
the profile. If your variant body contains strings that would
otherwise be misinterpreted as close tags, set
`requires_standard_close_when` on the `ParserVariant` itself — the
stream parser unions it across all enabled variants.

---

## Testing your variant

Minimum test coverage for a plugin variant:

1. **Match predicate** — positive / negative / leading whitespace /
   mismatched close / Unicode bodies.
2. **Parse function** — happy path, malformed JSON, wrong types,
   reserved name guard (if applicable), edge cases.
3. **End-to-end** — feed the variant through
   `parse_tool_calls(..., profile=profile_with_your_variant)` and
   verify the `ParsedToolCall` matches expected args.
4. **Stream parser** — construct `StreamParser` with your
   `custom_close_tags` / `requires_standard_close_when`, feed a
   representative stream in chunks, verify `TOOL_CALL` events
   appear.

The built-in test files under
`tests/test_tools/test_parser_variants_individual.py` and
`tests/test_tools/test_parser_variant_registry.py` are good models
to copy.

---

## Migration from pre-v13

Before v13 Phase A, `parsing._parse_xml` was a fixed if-ladder that
tried variants in a hardcoded sequence. In v13 Phase A:

- The leaf parse functions are unchanged — they moved intact into
  `parser_variants.py` as `ParserVariant.parse` callables.
- The hardcoded sequence moved into `DEFAULT_VARIANT_ORDER` so the
  legacy behaviour remains the default when a profile omits
  `parser_variants`.
- `parse_tool_calls(text, native, known_tool_names=..., *,
  profile=None)` is the one new-kwarg signature — all existing
  callers continue to work unchanged.

Phase B (profile migration) wires every `examples/model_profiles/
*.toml` to an explicit `[parser]` section. Phase C (cleanup)
deletes the deprecated fallback and the regex constants that are
no longer reached.

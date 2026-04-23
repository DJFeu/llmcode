# Breaking Changes — llmcode v2.2

This document enumerates every public-facing API change that lands in
v2.2. All changes listed here were previewed during the v2.0–v2.1
window and are deleted in v2.2 with no deprecation warning (the
deprecation period was v2.0 itself).

For the upgrade flow see [`upgrade_to_v2.md`](./upgrade_to_v2.md).
For the plugin codemod see
[`plugin_migration_guide.md`](./plugin_migration_guide.md).

---

## Symbol table

| Old symbol | New symbol | Removed in | Rationale |
|------------|-----------|-----------|-----------|
| `llm_code.runtime.tool_pipeline.LegacyToolExecutionPipeline` | `llm_code.engine.pipeline.Pipeline` | 2.2.0 | Compat shim for M1–M7 parity testing; legacy path fully replaced by engine. |
| `EngineConfig._v12_enabled: bool` | *removed* | 2.2.0 | Internal feature flag; all runs now flow through the engine path unconditionally. |
| `LLMCODE_V12` env var | *removed* | 2.2.0 | Transitional gate read only by the parity suite; no production code path read it after GA. |
| `llm_code.runtime.prompts.mode.*` (markdown templates) | `llm_code.engine.prompts.modes.*.j2` | 2.2.0 | Mode-specific system-reminder templates migrated to Jinja2 + `PromptBuilder`. |
| `llm_code.runtime.prompts.*.md` (model templates) | `llm_code.engine.prompts.models.*.j2` | 2.2.0 | Model-family intro prompts migrated to Jinja2 + `PromptBuilder`. |
| `llm_code.runtime.remote.server` | `llm_code.hayhooks.transport` (`enable_debug_repl = true`) | 2.2.0 | Headless RPC transport folded into the unified Hayhooks surface. |
| `llm_code.runtime.ide.server` | `llm_code.hayhooks.transport` (`enable_ide_rpc = true`) | 2.2.0 | IDE RPC transport folded into the unified Hayhooks surface. |
| Subclassing `ToolExecutionPipeline` | `@component` + `register(pipeline)` | 2.2.0 | Composable Component API (M2) replaces inheritance-based customisation. |
| `prompt.format(**kwargs)` on `runtime.prompts` strings | `PromptBuilder(template=prompt).run(**kwargs)["prompt"]` | 2.2.0 | Jinja2 rendering with `StrictUndefined` catches template/state drift. |
| `llm_code.memory.service.MemoryService` | `llm_code.engine.components.memory.*` | 2.2.0 | Memory retrieval and summarisation decomposed into Components. |
| `llm_code.memory.orchestration` | `llm_code.engine.components.memory.orchestration` | 2.2.0 | Same — Component-based orchestration. |
| Python 3.10 support | Python 3.11+ only | 2.2.0 | `tomllib` + `StrEnum` stdlib availability simplifies internals; 3.10 EOL per NEP 29. |
| `tests/test_engine/parity/` | *removed* | 2.2.0 | Parity tests needed a legacy path to compare against; no such path remains. |

---

## Config shape changes

### `EngineConfig`

Before (v2.0 / v2.1):

```python
EngineConfig(
    _v12_enabled=True,              # REMOVED
    agent_loop=AgentLoopConfig(),
    observability=ObservabilityConfig(),
    hayhooks=HayhooksConfig(),
    pipeline_stages=("perm", "denial", "rate", "speculative", "resolver", "exec", "post"),
)
```

After (v2.2):

```python
EngineConfig(
    agent_loop=AgentLoopConfig(),
    observability=ObservabilityConfig(),
    hayhooks=HayhooksConfig(),
    pipeline_stages=("perm", "denial", "rate", "speculative", "resolver", "exec", "post"),
)
```

Passing `_v12_enabled` as a keyword raises `TypeError` (dataclass
frozen-field enforcement).

---

## Rationale

### Why delete `_v12_enabled` at all?

The field existed so the parity test suite could run the legacy engine
and the new engine side by side and assert identical outputs. By v2.1
every parity scenario was green on the engine path; by v2.2 there is
no legacy path, so parity testing is vacuous. Keeping the field would
invite confusion ("is it safe to turn on?" — there is no off any more).

### Why move prompts to Jinja2?

`str.format`'s silent-substitution semantics caused two production
incidents in v1.23 where a template key was renamed but a call site
wasn't updated; the prompt silently rendered with the literal
`{missing_key}` token intact. Jinja2 with `StrictUndefined` makes that
class of bug impossible — a missing variable raises `UndefinedError`
at render time.

### Why delete the subclass API?

Inheritance-based customisation coupled plugins tightly to
`ToolExecutionPipeline`'s private layout. Every private-method rename
in the last two releases broke at least one plugin in the registry.
The Component API (M2) exposes a stable narrow interface (`run()`
returning a `dict`) plus pipeline-level wiring — plugins now fail
cleanly at registration time instead of silently drifting through
private-attribute mismatches.

### Why fold `remote/` and `ide/` into Hayhooks?

Three separate FastAPI apps with three separate auth schemes, three
separate rate-limit buckets, and three separate CORS policies was a
security-review nightmare. The v12 design folds them into one
transport with flag-gated endpoints; the attack surface is now one
port, one auth token, one rate-limit policy.

### Why drop Python 3.10?

3.10 reaches EOL in October 2026 per PEP 619. Removing the support
window early lets llmcode lean on `tomllib` (stdlib in 3.11+) without
shipping `tomli` as a runtime dep, and on `StrEnum` / improved
`typing` ergonomics. 3.10 users can pin to v2.1 until they migrate.

---

## Full spec references

- v12 design spec:
  `docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md`
  §5.8 (codemod), §9 R10 (legacy deletion), §9 R11 (release prep).
- v12 plan #8: `docs/superpowers/plans/2026-04-21-llm-code-v12-plugin-migration.md`.

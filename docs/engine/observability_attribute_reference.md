# Observability — span attribute reference

> **Policy:** every attribute name the engine puts on a span MUST live
> in `llm_code.engine.observability.attributes.ALLOWED_ATTRIBUTE_KEYS`
> and appear in the table below. `set_attr_safe` raises when an
> unlisted key is used at runtime.

## Pipeline / Component

| Constant                | Attribute name                   | Type    | Example                                   |
|-------------------------|----------------------------------|---------|-------------------------------------------|
| `PIPELINE_NAME`         | `llmcode.pipeline.name`          | string  | `"default"`                               |
| `COMPONENT_NAME`        | `llmcode.component.name`         | string  | `"PermissionCheck"`                       |
| `COMPONENT_INPUT_KEYS`  | `llmcode.component.input_keys`   | string[]| `["tool_name", "args"]`                   |
| `COMPONENT_OUTPUT_KEYS` | `llmcode.component.output_keys`  | string[]| `["allowed", "reason"]`                   |

## Agent loop

| Constant           | Attribute name                  | Type    | Example                  |
|--------------------|---------------------------------|---------|--------------------------|
| `AGENT_ITERATION`  | `llmcode.agent.iteration`       | int     | `3`                      |
| `AGENT_MODE`       | `llmcode.agent.mode`            | string  | `"plan"`                 |
| `AGENT_EXIT_REASON`| `llmcode.agent.exit_reason`     | string  | `"max_steps"`            |
| `AGENT_DEGRADED`   | `llmcode.agent.degraded`        | bool    | `true`                   |

## Tool call

| Constant             | Attribute name                | Type    | Example                            |
|----------------------|-------------------------------|---------|------------------------------------|
| `TOOL_NAME`          | `llmcode.tool.name`           | string  | `"bash"`                           |
| `TOOL_ARGS_HASH`     | `llmcode.tool.args_hash`      | string  | `"a1b2c3d4e5f60718"` (SHA-256/16)   |
| `TOOL_RESULT_IS_ERROR`| `llmcode.tool.result_is_error`| bool   | `false`                            |
| `TOOL_RETRY_ATTEMPT` | `llmcode.tool.retry_attempt`  | int     | `2`                                |
| `TOOL_FALLBACK_FROM` | `llmcode.tool.fallback_from`  | string  | `"playwright.navigate"`            |

**Never** set raw tool arguments — use `args_hash(args)` instead. Raw
arguments can contain PII, URLs with embedded tokens, etc.

## Model / API (OpenTelemetry GenAI conventions)

| Constant             | Attribute name                   | Type    | Example                |
|----------------------|----------------------------------|---------|------------------------|
| `MODEL_NAME`         | `gen_ai.request.model`           | string  | `"claude-sonnet-4"`    |
| `MODEL_PROVIDER`     | `gen_ai.system`                  | string  | `"anthropic"`          |
| `TOKENS_IN`          | `gen_ai.usage.prompt_tokens`     | int     | `1024`                 |
| `TOKENS_OUT`         | `gen_ai.usage.completion_tokens` | int     | `512`                  |
| `MODEL_TEMPERATURE`  | `gen_ai.request.temperature`     | float   | `0.2`                  |

Token usage may also be recorded as span *events* (not attributes) so
streamed chunks carry per-chunk timestamps.

## Session

| Constant            | Attribute name                | Type    | Example          |
|---------------------|-------------------------------|---------|------------------|
| `SESSION_ID`        | `llmcode.session.id`          | string  | `"sess-abc123"`  |
| `USER_PROMPT_LENGTH`| `llmcode.user_prompt.length`  | int     | `128`            |

`USER_PROMPT_LENGTH` records only the **character length** of the
prompt — the prompt content must never appear on a span.

## Adding a new attribute

1. Add the constant to `llm_code.engine.observability.attributes`.
2. Append it to `ALLOWED_ATTRIBUTE_KEYS`.
3. Document it in this file (name + type + example).
4. Add a test in `tests/test_engine/observability/test_attributes.py`.

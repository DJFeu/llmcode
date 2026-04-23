# Upgrade to llmcode v2.2

This guide walks you through upgrading from llmcode v2.1.x (or any
v1.23.x) to v2.2. The upgrade removes every remaining v1-era shim and
is the final cutover for the v12 Haystack-borrow overhaul.

**Bottom line for most users:** `pip install -U llmcode-cli` — session
files, HIDA indices, and slash commands all continue to work.

---

## Audience 1 — End users

You run llmcode as a CLI (`llmcode …` in your terminal) and don't
maintain plugins. Your upgrade path is:

```bash
# 1. Confirm you are on Python >= 3.11
python3 --version   # → must say 3.11, 3.12, or 3.13

# 2. Upgrade the package
pip install -U llmcode-cli

# 3. Smoke-test
llmcode --version   # should say 2.2.0
llmcode             # should land you in the REPL
```

Your session files (`.llmcode/sessions/*.json`), HIDA index
(`.llmcode/hida/*.json`), and slash-command inventory carry over
verbatim. Nothing to migrate on disk.

**If you are still on Python 3.10**, pip's dependency resolver will
refuse the install. Two options:

- Recommended: install Python 3.11+ via your OS package manager,
  `pyenv`, or `uv python install 3.11`.
- If you absolutely must stay on 3.10: pin `llmcode-cli==2.1.0` (the
  last release that supported 3.10).

---

## Audience 2 — Plugin authors

You ship a plugin that imports from `llm_code.runtime.tool_pipeline`,
subclasses `ToolExecutionPipeline`, or imports from
`llm_code.runtime.prompts.mode`. These paths are removed in v2.2.

### Migration workflow

```bash
# 1. Install the codemod helper (included in v2.2)
pip install -U "llmcode-cli[migrate]"

# 2. Dry-run against your plugin
llmcode migrate v12 ./path/to/plugin --dry-run

# 3. Review the unified diff; fix any patterns the codemod flagged
#    as "unsupported" by hand (see docs/plugin_migration_guide.md).

# 4. Apply the codemod in place
llmcode migrate v12 ./path/to/plugin

# 5. Run your plugin test suite to verify the rewrite
pytest ./path/to/plugin/tests/
```

The codemod rewrites four common shapes — `ToolExecutionPipeline`
subclass → `@component`; `runtime.prompts.mode` imports →
`PromptBuilder(template_path=...)`; `prompt.format(...)` calls →
`PromptBuilder(template=...).run(...)`; and the `llmcode` dependency
constraint in your `pyproject.toml` to `>=2.0,<3.0`.

Full walkthrough: [docs/plugin_migration_guide.md](./plugin_migration_guide.md).

Complete symbol mapping: [docs/breaking_changes_v2.md](./breaking_changes_v2.md).

---

## Audience 3 — REPL users

If you drive llmcode through the interactive REPL, no action is
required. The REPL entry point (`llmcode`) is unchanged; the cutover
happens behind the pipeline façade.

---

## Audience 4 — Hayhooks adopters

If you run llmcode as a headless service via `llmcode serve-hayhooks`,
v2.2 absorbs the previously separate `remote/server.py` and
`ide/server.py` into a single Hayhooks transport. Your existing
OpenAI-compat and MCP endpoints are unchanged.

New endpoints available:

- `/ide/rpc/*` — formerly `llmcode serve-ide` (set
  `hayhooks.enable_ide_rpc = true`).
- `/debug/repl` — formerly `llmcode serve-remote` (set
  `hayhooks.enable_debug_repl = true`; **defaults to false** because
  the REPL endpoint executes arbitrary Python).

Config example:

```toml
[hayhooks]
enabled = true
enable_openai_compat = true
enable_mcp = true
enable_ide_rpc = true
enable_debug_repl = false   # leave off in production
host = "127.0.0.1"
port = 8080
```

Audit your firewall rules and auth-token setup before flipping the
toggles — `enable_debug_repl = true` on a public interface is a
full-system RCE footgun.

---

## Optional extras — pick what you need

v2.2 ships a refined matrix of `[project.optional-dependencies]`. Core
install stays lean; the heavy dependencies are opt-in:

```bash
# Just the runtime (no Prometheus, no Langfuse, no Hayhooks)
pip install llmcode-cli

# OpenTelemetry exporters + Langfuse + Prometheus metrics (~20 MB)
pip install 'llmcode-cli[observability]'

# Hayhooks OpenAI-compat / MCP server
pip install 'llmcode-cli[hayhooks]'

# Multi-layer memory w/ local embeddings (heavy torch stack)
pip install 'llmcode-cli[memory]'

# ONNX reranker (faster, no torch dep)
pip install 'llmcode-cli[memory-rerank]'

# Plugin migration codemod (libcst + tomlkit)
pip install 'llmcode-cli[migrate]'

# Kitchen-sink install — observability + hayhooks + memory + migrate
pip install 'llmcode-cli[all]'

# Compose extras in one shot
pip install 'llmcode-cli[observability,hayhooks]'
```

`[telemetry]` is kept as a backwards-compatible alias for
`[observability]` so existing install scripts keep working.

The OpenTelemetry **API + SDK** themselves ship in the core install —
every build can emit spans; only the *exporters* (OTLP, Langfuse) and
metrics backend (prometheus_client) are gated behind `[observability]`.

---

## Step-by-step upgrade

1. **Backup.** Make a one-command snapshot of your session state in
   case a rollback is needed:

   ```bash
   cp -r ~/.llmcode ~/.llmcode.v21.bak
   ```

2. **Upgrade the package.**

   ```bash
   pip install -U llmcode-cli
   ```

3. **Run the memory migration (optional, no-op for post-v2.0 users).**

   ```bash
   llmcode memory migrate
   ```

   Safe to re-run; idempotent.

4. **Verify.**

   ```bash
   llmcode --version      # should report 2.2.0
   llmcode doctor         # should report all green
   ```

5. **Resume work.** Your previous REPL session resumes cleanly via the
   standard recovery path:

   ```bash
   llmcode --resume
   ```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'llm_code.runtime.prompts.mode'"

A dependency (probably a plugin) still imports from the legacy prompt
path. Locate it with `pip show <plugin-name>`, then either upgrade the
plugin to its v12-compatible release or run the codemod against the
plugin source tree yourself (plugin authors: see Audience 2 above).

### "AttributeError: EngineConfig has no field '_v12_enabled'"

Your fixture / test scaffold constructs `EngineConfig(_v12_enabled=...)`.
Drop the kwarg — all paths run through the engine unconditionally now:

```python
# Before
cfg = EngineConfig(_v12_enabled=True)

# After
cfg = EngineConfig()
```

### "Legacy prompt directory runtime/prompts/ still on disk"

The smoke test `tests/test_no_legacy_references.py` will flag a partial
install where the v2.2 wheel dropped the new templates but an older
`.pyc` or editable-install remnant still has the legacy `.md` files.
Clean install fixes it:

```bash
pip uninstall -y llmcode-cli
pip install -U --no-cache-dir llmcode-cli
```

### "pip install fails with 'requires Python >=3.11'"

You're on 3.10 (or older). Upgrade Python — see the note in Audience
1. If you must stay on 3.10, pin `llmcode-cli==2.1.0`.

---

## Rollback plan

If v2.2 breaks something critical in your workflow:

```bash
pip install "llmcode-cli==2.1.0"
rm -rf ~/.llmcode              # optional: drop v2.2 cache
cp -r ~/.llmcode.v21.bak ~/.llmcode
```

Session files from v2.2 are forward-compatible with v2.1 (the session
schema did not change in v2.2), so you can downgrade without data
loss.

If the downgrade is required, please open an issue at
https://github.com/DJFeu/llmcode/issues so we can fix the blocker
before the next release.

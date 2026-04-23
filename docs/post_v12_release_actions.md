# Post-v12 Release — Ecosystem & Manual Actions

This checklist covers steps that **cannot be automated** by the subagent
pipeline and require human judgement, external systems, or elapsed
calendar time. It is intended as an operational runbook between the end
of the v12 engine development push and the public v2.0 GA tag.

**Context:** v12 engine overhaul (M0–M8) is code-complete with 7260+
tests green. What remains is a standard library-release discipline:
plugin ecosystem migration, threat review, publish, soak, monitor.

---

## 1. Plugin ecosystem migration (plan #8 Task 8.a.7)

**Why this is manual:** each registered plugin lives in its own repo
with its own maintainer. The codemod is fully tested locally but needs
to be applied in-situ and the output PRs opened in each plugin repo.

**Registry source of truth:** `project_plugin_system.md` memory entry
(last verified 2026-04-06) lists the three currently registered
plugins. Look up before acting — the registry may have churned.

**Per-plugin workflow:**

1. Clone plugin repo into a scratch worktree.
2. Run `llmcode migrate v12 <plugin_path> --dry-run --report migration-report.json`.
3. Review the unified diff + diagnostics. Address any "unsupported
   pattern" findings manually per `docs/plugin_migration_guide.md`.
4. Apply: `llmcode migrate v12 <plugin_path>`.
5. Run the plugin's own test suite; fix anything the codemod missed.
6. Open a PR in the plugin repo titled
   `chore: migrate to llmcode v2 engine` with the migration-report.json
   attached as a comment.
7. Once merged, release a new plugin version pinned to `llmcode>=2.0`.
8. Update the central plugin registry with the new version number.

**Idempotence check:** re-run `llmcode migrate v12 <plugin_path>` on the
migrated tree; expect zero changes (the codemod guarantees
idempotence via the runner's self-test).

---

## 2. Threat model review — Adam sign-off (plan #4 Task 4.10)

Review the Hayhooks attack surface end-to-end before the first GA tag
binds network ports in user environments. Scope:

- **Auth** — bearer token storage (`LLMCODE_HAYHOOKS_TOKEN`), rotation
  expectations, accidental commit of `.env` files.
- **Network bind** — default `127.0.0.1`, `--allow-remote` flag
  semantics, documentation guidance around reverse-proxy with TLS.
- **Input size caps** — 1 MB body, 100 messages, 413/400 envelopes.
- **Rate limit** — 60 rpm per fingerprint, 429 envelope with
  `Retry-After`.
- **Prompt injection boundary** — hayhooks does *not* sanitize prompts;
  callers are responsible for authority boundaries. Verify this is
  prominently documented in `docs/hayhooks/security.md`.
- **Redaction coverage** — confirm the 52-pattern corpus (in
  `tests/test_engine/observability/fixtures/leak_corpus.txt`) covers
  any new secret types Anthropic/OpenAI have started minting since
  the corpus was captured.
- **MCP transport** — stdio is trust-the-parent; SSE inherits bearer
  auth. Verify no MCP-specific bypass path.

**Output:** a one-page threat model note committed as
`docs/security/v2_threat_model.md` with `Approved by: Adam` header and
a date. Until this file exists, do **not** tag v2.0.0.

---

## 3. Version tag, build, publish (plan #8 Task 8.c.5)

### 3.1 Release candidate — `v2.2.0rc1`

The codebase is already at `version = "2.2.0rc1"` in `pyproject.toml`.
Steps:

```bash
cd /Users/adamhong/Work/qwen/llm-code
git status                         # expect clean tree
git tag -a v2.2.0rc1 -m "v2 engine overhaul — release candidate 1"
git push origin v2.2.0rc1

.venv/bin/python -m build          # builds sdist + wheel
.venv/bin/twine check dist/*
.venv/bin/twine upload --repository testpypi dist/*   # dogfood first
.venv/bin/twine upload dist/*      # real PyPI when confident
```

### 3.2 1-week internal soak (Adam dogfood)

Run `llmcode` as your daily driver for 7 calendar days. Track:

- any crash or silent regression vs v1.23.x
- token-count / latency per turn (M6 observability makes this visible)
- any plugin that fails to load despite migration

File issues against any regression. If any CRITICAL-class issue
surfaces, cut `v2.2.0rc2` and restart the soak window.

### 3.3 External preview

- Draft a GitHub release on the `v2.2.0rc1` tag with the
  `CHANGELOG.md` v2 section copied into the body.
- Pin the release as "pre-release".
- Announce to plugin authors with a link to
  `docs/plugin_migration_guide.md` and the codemod invocation.

### 3.4 GA — `v2.2.0`

After ≥7 days of green soak + no rc2 cuts:

```bash
# Bump pyproject.toml: version = "2.2.0"
git commit -am "chore: bump version → 2.2.0 (GA)"
git tag -a v2.2.0 -m "v2 engine overhaul — general availability"
git push origin v2.2.0
.venv/bin/python -m build
.venv/bin/twine upload dist/*
```

Update the GitHub release to "latest" and unpin the rc1 pre-release
label.

---

## 4. Post-release monitoring (plan #8 Task 8.c.6)

**First 48 hours:** watch the issue tracker hourly during business
hours. Any `ModuleNotFoundError` / `ImportError` related to removed
legacy modules is a **P0** — cut a `2.2.1` patch with a compat shim
the same day.

**First 2 weeks:** confirm all 3 registered plugins publish v12-compat
releases. If any plugin maintainer is unresponsive, follow the
"community fork" playbook in plan #8 §R2: fork under the llmcode org,
credit original author, update the registry entry.

**First month:** via the M6 Langfuse dashboard, watch for error
classes that didn't exist pre-v12:

- `AsyncPipeline` cancellation leaks (trace span "cancelled" events)
- Component wiring mismatches (SocketMismatchError in logs)
- Redaction false negatives (any span attribute still matching a
  leak-corpus regex after the redactor ran)

If any class shows ≥3 distinct reports, file a tracking issue and
schedule a v2.2.1 or v2.3.0 fix.

---

## 5. Memory + session state refresh

After GA tag:

- Update `project_llm_code.md` memory entry: version → 2.2.0,
  test count → 7260+, new top-line features (engine DAG, hayhooks,
  async, observability, memory-as-Component).
- Update `project_llm_code_v2_repl_rewrite.md`: note the engine
  overhaul landed alongside the REPL rewrite in 2026-Q2.
- Mark `project_llm_code_borrow_targets.md` as superseded (most
  borrow targets were absorbed into v12 M4/M6/M7).

---

## 6. Nice-to-have (explicitly not blocking GA)

- **`claude-code` feature parity audit** — v12 got us close to parity
  on engine architecture; a line-by-line audit vs current claude-code
  is a separate v2.3 scope.
- **Hayhooks `[enterprise]` extra** — SSO, audit log, multi-tenant
  rate limiting. Post-v2.2 if market demand appears.
- **Memory ONNX model bundling** — currently `onnx` backend falls back
  to deterministic hash when the model asset isn't present. Decision:
  ship model via optional `llmcode[memory-rerank-bundled]` extra that
  pulls HuggingFace cache, OR keep as env-var `LLMCODE_ONNX_MODEL_PATH`
  only. Deferred to v2.3.
- **Async tool migration** — only `WebFetch` is async-native; extending
  to `WebSearch` backends, `Bash` (via `asyncio.create_subprocess_exec`)
  is a v2.3 scope.

---

**End of post-v12 release actions.**

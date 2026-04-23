# Memory Migration Guide (HIDA v10 → v12)

> **Status:** v12 M7 — the HIDA index format moves from schema
> version 1/2 (v10/v11) to version 3 (v12). A one-shot
> `llmcode memory migrate` command streams the old index into the
> new shape. The legacy format is still readable by the migrator
> but will not be accepted by the v12 `MemoryLayer` runtime.

## Section table of contents

1. When to migrate
2. Backup first
3. Run the migration
4. Verify the output
5. Embedder-model drift
6. Rollback
7. Troubleshooting

## 1. When to migrate

Migrate any time you have a HIDA index written by v10 or v11 and
you want to enable the v12 memory Components (see
[memory_components.md](memory_components.md)). The migrator
detects the schema version from the first line of the file
(`{"magic": "HIDA_IDX", "schema_version": <N>}`) and decides:

| Detected version | Migrator action |
|------------------|------------------|
| 1 or 2 | Convert to v12 (schema_version = 3). |
| 3 | No-op; logs "already v12". |
| 0 / missing / malformed header | `ValueError` — the file is either corrupt or not a HIDA index. |

## 2. Backup first

The migrator writes a **new** file at the destination path; the
source is read-only. Still, always keep a backup until you've
verified the new index:

```bash
cp ~/.llmcode/hida/index.jsonl ~/.llmcode/hida/index.jsonl.v10.bak
```

If the v10 index is already checked into a snapshot repo, prefer
a snapshot clone over a file copy so concurrent writes can't
corrupt the backup.

## 3. Run the migration

Using the click CLI (once wired into the top-level `llmcode`
binary in a follow-up milestone; until then invoke the module
directly):

```bash
python -m llm_code.memory.cli memory migrate \
    --from ~/.llmcode/hida/index.jsonl \
    --to   ~/.llmcode/hida/index.v12.jsonl
```

Dry-run mode (no destination file is written; counts + warnings
still surface):

```bash
python -m llm_code.memory.cli memory migrate \
    --from ~/.llmcode/hida/index.jsonl \
    --to   ~/.llmcode/hida/index.v12.jsonl \
    --dry-run
```

Example output:

```
entries_read=1824 entries_written=1824 schema_from=2 schema_to=3 duration_s=0.3412
warnings (3):
  - Unknown legacy field 'legacy_scope' on entry id='abc123' preserved under metadata.unknown_legacy
  - Unknown legacy field 'v9_session_id' on entry id='abc123' preserved under metadata.unknown_legacy
  - Unknown legacy field 'legacy_scope' on entry id='def456' preserved under metadata.unknown_legacy
```

The migrator guarantees:

- **Streaming**: the source is read one line at a time so memory
  stays flat regardless of index size.
- **No data loss**: legacy fields outside the known set
  (`id`, `text`, `embedding`, `source`, `created_at`, `scope`)
  are preserved under `metadata.unknown_legacy` and each field
  name is surfaced as a warning.
- **UTC normalisation**: naive datetimes are pinned to UTC so
  downstream comparisons don't raise `TypeError`.
- **Source split**: the legacy `source = "tool:<name>"` field is
  split on the first `:` into `source_tool` (head); the raw
  string is preserved under `metadata.source_raw` for audit.
- **Provenance stamp**: every migrated entry gets
  `metadata.migrated_from_v10 = True`.

## 4. Verify the output

### Schema header

```bash
head -n 1 ~/.llmcode/hida/index.v12.jsonl
# {"magic": "HIDA_IDX", "schema_version": 3}
```

### Entry count

```bash
wc -l ~/.llmcode/hida/index.jsonl ~/.llmcode/hida/index.v12.jsonl
```

The v12 line count should be `entries_read + 1` (one header +
N entries). A mismatch usually means the source had corrupt
lines that were silently skipped — compare with the report's
`entries_read` number.

### Vector dimension check

If the legacy index was written against a different embedder
model, the stored vectors won't match the v12 embedder's
dimension. Quick check:

```python
import json

with open("~/.llmcode/hida/index.v12.jsonl") as fh:
    _header = fh.readline()
    first = json.loads(fh.readline())
    print("stored dimension:", len(first["embedding"] or []))

from llm_code.engine.components.memory.embedder import build_embedder_from_config
from llm_code.runtime.config import load_config

cfg = load_config()
embedder = build_embedder_from_config(cfg.engine.memory)
print("runtime dimension:", embedder.dimension)
```

A mismatch means you need to either (a) keep the old embedder
model configured, or (b) re-embed — the v12 writer will overwrite
stale embeddings on next write, but retrieval over mismatched
dimensions fails the cosine-similarity guard in `InMemoryMemoryLayer`
with `ValueError: Cosine similarity dimension mismatch: X != Y`.

### Sanity-read one entry

```python
with open("~/.llmcode/hida/index.v12.jsonl") as fh:
    fh.readline()     # skip header
    entry = json.loads(fh.readline())
    assert entry["scope"] in ("session", "project", "global")
    assert entry["metadata"].get("migrated_from_v10") is True
```

## 5. Embedder-model drift

Changing the `embedder_model` after migration invalidates the
stored vectors. The migrator does NOT re-embed — it copies the
legacy vectors verbatim. If you're also switching models:

1. Finish the schema migration first (v10 → v12).
2. In `config.json`, set
   `engine.memory.embedder_model = "<new model>"`.
3. Re-embed by running a full re-index (implementation-dependent;
   the v12 writer re-embeds on next write, but a one-shot
   rebuild helper is tracked as a follow-up task).

Running retrieval with drifted dimensions trips the
`ValueError: Cosine similarity dimension mismatch` guard — that
is an intentional fail-fast, not a bug.

## 6. Rollback

The migrator is a one-way transformation, but rollback is
mechanical because the source file is untouched:

1. Stop any llmcode process writing to the v12 index.
2. Point the config back at the v10 index path, or restore
   from `index.jsonl.v10.bak`.
3. Downgrade llmcode to the v11 line (`pip install
   "llmcode==1.23.*"`) so the reader supports
   `schema_version<=2`.

The v12 `MemoryLayer` rejects legacy headers at load time, so a
mixed-version fleet is not supported.

## 7. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `FileNotFoundError: Source index not found` | Path typo; the migrator does not create missing inputs. |
| `ValueError: Could not detect schema version` | File is empty, missing the magic line, or not JSON. Inspect `head -n 1` of the file. |
| `entries_read < entries_written` (impossible) | Bug — please file an issue with the full report. |
| `entries_read > entries_written` | The source had blank or unparseable lines; the migrator silently skipped them. |
| Warnings listing `unknown_legacy` fields | Expected on v10 indexes with custom metadata; every skipped field is preserved. |
| Destination already v12 | Safe no-op; re-running the migrator is idempotent. |

The migrator is intentionally conservative — when in doubt it
preserves data and logs a warning rather than fail. Read
`llm_code/memory/migrate.py::migrate_index` for the full
per-entry conversion contract.

"""``llmcode migrate v2.6 state-db`` — JSON checkpoints → SQLite (v16 M10).

Walks ``~/.llmcode/checkpoints/*.json`` (or a custom directory),
inserts every checkpoint into a new ``state.db.tmp``, and atomically
renames it to ``state.db`` once every row has landed. Original JSON
files are moved to ``~/.llmcode/checkpoints.bak/<timestamp>/`` so the
migration is fully reversible by hand.

The command is OPT-IN — the runtime never auto-runs it. Users invoke
it once after upgrading to v2.6.

Mitigation for spec R5 (mid-migration disk full): the temp DB is
populated from scratch in a single transaction; on any exception we
delete the temp file and leave the originals untouched. Only after
the transaction commits do we (1) rename temp → state.db and
(2) move originals into the backup directory.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

import click

from llm_code.runtime.state_db import StateDB

logger = logging.getLogger(__name__)


def migrate_checkpoints_to_state_db(
    checkpoints_dir: Path,
    state_db_path: Path,
    backup_root: Path,
) -> dict:
    """Run the migration. Returns a summary dict.

    Atomic flow:

    1. Build ``state_db_path.with_suffix(".db.tmp")`` from scratch.
    2. Insert one row per JSON checkpoint inside a single transaction.
    3. On error: delete the temp file, raise, originals untouched.
    4. On success: ``temp → state.db`` rename, then move originals to
       the backup directory.
    """
    if not checkpoints_dir.exists():
        return {"migrated": 0, "skipped": 0, "backup": None}

    files = sorted(checkpoints_dir.glob("*.json"))
    tmp_path = state_db_path.with_suffix(".db.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / timestamp

    db = StateDB(tmp_path)
    migrated = 0
    skipped = 0
    try:
        conn = db._ensure_open()  # noqa: SLF001 — migration owns the DB
        conn.execute("BEGIN")
        try:
            for path in files:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "migrate_v26: skipping %s: %s", path.name, exc
                    )
                    skipped += 1
                    continue
                session_id = str(payload.get("id") or path.stem)
                project_path = str(payload.get("project_path", ""))
                model = payload.get("cost_tracker", {}).get("model") if isinstance(
                    payload.get("cost_tracker"), dict
                ) else None
                db.upsert_session(
                    session_id=session_id,
                    payload=payload,
                    model=model,
                    project_path=project_path,
                )
                migrated += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except Exception:
        db.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    finally:
        db.close()

    # Atomic rename — Path.rename is atomic on POSIX for same-filesystem
    # moves. If the destination exists we replace it (an old aborted
    # migration left a state.db behind).
    if state_db_path.exists():
        state_db_path.unlink()
    tmp_path.rename(state_db_path)

    # Move originals into the backup directory after the rename so a
    # crash mid-backup leaves the migrated DB intact.
    if files:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            try:
                shutil.move(str(path), backup_dir / path.name)
            except OSError as exc:
                logger.warning(
                    "migrate_v26: failed to back up %s: %s", path.name, exc
                )

    return {
        "migrated": migrated,
        "skipped": skipped,
        "backup": str(backup_dir) if files else None,
    }


# ── click integration ─────────────────────────────────────────────────


@click.command(
    name="state-db",
    help=(
        "v2.6 migration: copy JSON checkpoints to ~/.llmcode/state.db. "
        "Originals moved to ~/.llmcode/checkpoints.bak/<timestamp>/."
    ),
)
@click.option(
    "--checkpoints-dir", type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--state-db", type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--backup-root", type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def state_db_command(
    checkpoints_dir: Path | None,
    state_db: Path | None,
    backup_root: Path | None,
) -> None:
    """Run the JSON → SQLite migration once."""
    home_llmcode = Path.home() / ".llmcode"
    src = checkpoints_dir or (home_llmcode / "checkpoints")
    dst = state_db or (home_llmcode / "state.db")
    backup = backup_root or (home_llmcode / "checkpoints.bak")
    summary = migrate_checkpoints_to_state_db(src, dst, backup)
    click.echo(json.dumps(summary, indent=2))

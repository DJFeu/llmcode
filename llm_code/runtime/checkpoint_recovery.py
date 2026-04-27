"""Session checkpoint recovery: save/load full session state for crash recovery.

v16 M10 introduces an optional SQLite-backed store
(:class:`llm_code.runtime.state_db.StateDB`). When the user has run
``llmcode migrate v2.6 state-db``, the per-session ``state.db``
becomes the source of truth and JSON files only persist as a fallback
for users who have not migrated yet.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.session import Session
    from llm_code.runtime.state_db import StateDB

logger = logging.getLogger(__name__)

_CHECKPOINTS_DIR_NAME = "checkpoints"


class CheckpointRecovery:
    """Persist and restore full session state for crash recovery.

    By default checkpoints live as JSON files under
    ``~/.llmcode/checkpoints/<session_id>.json``. Pass a
    :class:`StateDB` (v16 M10) to write/read through SQLite instead;
    the JSON path is preserved as a read-fallback so unmigrated
    machines keep working.
    """

    def __init__(
        self,
        checkpoints_dir: Path,
        state_db: "StateDB | None" = None,
    ) -> None:
        self._dir = checkpoints_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_db = state_db
        self._auto_save_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Core persistence
    # ------------------------------------------------------------------

    def save_checkpoint(
        self,
        session: "Session",
        cost_tracker: "object | None" = None,
    ) -> Path:
        """Serialize *session* to disk and return the checkpoint file path.

        When ``cost_tracker`` is provided, its accumulated token/cost
        state is included so a resumed session continues from the
        correct running total instead of resetting to zero.
        """

        data = session.to_dict()
        data["checkpoint_saved_at"] = datetime.now(timezone.utc).isoformat()
        if cost_tracker is not None and hasattr(cost_tracker, "to_dict"):
            data["cost_tracker"] = cost_tracker.to_dict()

        # v16 M10 — when a StateDB is wired, write through it so
        # subsequent loads can use the single SQLite source of truth.
        # JSON file is still written for back-compat (users mid-migration
        # may roll back to v2.5.x and expect the JSON to be there).
        if self._state_db is not None:
            try:
                self._state_db.upsert_session(
                    session_id=session.id,
                    payload=data,
                    project_path=str(getattr(session, "project_path", "")),
                )
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning("state_db.upsert failed: %s", exc)

        path = self._dir / f"{session.id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Checkpoint saved: %s", path)
        return path

    def load_checkpoint(
        self,
        session_id: str,
        *,
        cost_tracker: "object | None" = None,
    ) -> "Session | None":
        """Deserialize a checkpoint by *session_id*, or return None.

        When ``cost_tracker`` is provided and the checkpoint carries a
        ``cost_tracker`` payload (written by a previous :meth:`save_checkpoint`
        call), the tracker is restored in-place via
        ``restore_from_dict`` so a resumed session continues from the
        correct running token / cost total instead of resetting to zero.
        Callers that don't care about cost continuity can omit the
        argument — the old ``load_checkpoint(session_id)`` signature
        still works.
        """
        from llm_code.runtime.session import Session  # local import to avoid cycles

        # v16 M10 — try the SQLite store first when wired so migrated
        # sessions resume from there even if the legacy JSON file is
        # gone. Falls back to JSON when the row is missing or the
        # store wasn't injected.
        if self._state_db is not None:
            payload = self._state_db.load_session(session_id)
            if payload is not None:
                payload.pop("checkpoint_saved_at", None)
                cost_data = payload.pop("cost_tracker", None)
                try:
                    session = Session.from_dict(payload)
                except (KeyError, TypeError) as exc:
                    logger.warning(
                        "state_db payload for %s rejected: %s — falling back to JSON",
                        session_id,
                        exc,
                    )
                else:
                    if (
                        cost_tracker is not None
                        and cost_data is not None
                        and hasattr(cost_tracker, "restore_from_dict")
                    ):
                        try:
                            cost_tracker.restore_from_dict(cost_data)
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.debug(
                                "cost_tracker.restore_from_dict (state_db) failed for %s: %s",
                                session_id,
                                exc,
                            )
                    return session

        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("checkpoint_saved_at", None)
            cost_data = data.pop("cost_tracker", None)
            # Route through session_migration so older schema versions load
            try:
                from llm_code.runtime.session_migration import load_and_migrate
                data["messages"] = load_and_migrate(path)
            except Exception:  # pragma: no cover - defensive
                pass
            session = Session.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load checkpoint %s: %s", session_id, exc)
            return None

        # Wave2-2: restore cost tracker running totals if the caller
        # supplied one and the checkpoint was written with cost state.
        if (
            cost_tracker is not None
            and cost_data is not None
            and hasattr(cost_tracker, "restore_from_dict")
        ):
            try:
                cost_tracker.restore_from_dict(cost_data)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "cost_tracker.restore_from_dict failed for %s: %s",
                    session_id,
                    exc,
                )

        return session

    def list_checkpoints(self) -> list[dict]:
        """Return checkpoint descriptors sorted by modification time (newest first).

        Each dict has: ``session_id``, ``saved_at``, ``message_count``,
        ``project_path``, ``updated_at``.
        """
        results: list[dict] = []
        for path in sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "session_id": data.get("id", path.stem),
                    "saved_at": data.get("checkpoint_saved_at", ""),
                    "message_count": len(data.get("messages", [])),
                    "project_path": data.get("project_path", ""),
                    "updated_at": data.get("updated_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def delete_checkpoint(self, session_id: str) -> bool:
        """Delete a checkpoint file; returns True if it existed."""
        path = self._dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Auto-save background task
    # ------------------------------------------------------------------

    def start_auto_save(self, get_session_fn, interval: int = 60) -> None:
        """Start a background asyncio task that saves a checkpoint every *interval* seconds.

        *get_session_fn* is a zero-argument callable that returns the current
        :class:`~llm_code.runtime.session.Session` (or None to skip).
        """
        if self._auto_save_task is not None and not self._auto_save_task.done():
            return  # already running

        async def _loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    session = get_session_fn()
                    if session is not None:
                        self.save_checkpoint(session)
                except Exception as exc:
                    logger.debug("Auto-save checkpoint error: %s", exc)

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return

        self._auto_save_task = loop.create_task(_loop())
        logger.debug("Checkpoint auto-save started (interval=%ds)", interval)

    def stop_auto_save(self) -> None:
        """Cancel the auto-save background task if running."""
        if self._auto_save_task is not None and not self._auto_save_task.done():
            self._auto_save_task.cancel()
            self._auto_save_task = None

    # ------------------------------------------------------------------
    # Startup detection
    # ------------------------------------------------------------------

    def detect_last_checkpoint(
        self,
        *,
        cost_tracker: "object | None" = None,
    ) -> "Session | None":
        """Return the most recently modified checkpoint session, or None.

        Forwards ``cost_tracker`` to :meth:`load_checkpoint` so the
        caller can opt in to restoring the running cost total at the
        same time as the session.
        """
        entries = self.list_checkpoints()
        if not entries:
            return None
        return self.load_checkpoint(
            entries[0]["session_id"], cost_tracker=cost_tracker
        )

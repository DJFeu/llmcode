"""Persistent cross-session cache for SkillRouter Tier C results.

Tier C is the LLM classifier fallback (`_classify_with_llm_debug`).
It burns 5-15 seconds per invocation because it sends a full request
to the model asking "which skill matches this user input". Before
this cache, the in-memory ``self._cache`` on ``SkillRouter`` was
discarded on every TUI restart, so re-asking the same CJK query in a
fresh session paid the Tier C cost AGAIN.

With this cache, the answer is persisted to
``~/.llmcode/skill_router_cache.json`` keyed by ``(skill_set_hash,
query_prefix)``. Second session hits the file, reads the cached
result, and Tier C is skipped entirely.

Key design points:

- **``skill_set_hash``** is a SHA1 of the sorted skill name list.
  When the user adds or removes skills, the hash changes, and the
  old cache entries are abandoned — stale answers can't point at
  deleted skills or miss new ones.

- **``query_prefix``** is ``user_message[:200]`` matching the
  existing in-memory cache key convention. Long messages differ
  only after index 200, which almost never happens in practice.

- **Negative caching**: a Tier C call that returns "no skill
  matched" stores ``None``. The next lookup returns it immediately
  instead of re-running the 14s LLM call.

- **Entry cap**: the file is capped at 500 entries per
  ``skill_set_hash``. When the cap is reached on write, the oldest
  entries (by ``cached_at``) are dropped. This keeps the file
  bounded for users who ask many unique CJK questions.

- **Atomic writes** via ``tempfile.mkstemp`` + ``os.replace`` so a
  concurrent reader never sees a partial write.

- **All failures are swallowed** and logged at DEBUG — this is a
  pure optimization, not a correctness boundary. A missing or
  corrupt cache just means the user pays the 14s Tier C cost one
  more time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_log = logging.getLogger(__name__)

_CACHE_PATH = Path.home() / ".llmcode" / "skill_router_cache.json"

# Hard cap on entries per skill_set_hash bucket. Beyond this, the
# oldest entries (by cached_at) are dropped on write.
_MAX_ENTRIES = 500

# Sentinel returned from ``load`` when the cache has no answer for
# the query. Distinct from ``None`` which means "Tier C returned
# negatively, skip it again".
NOT_CACHED = object()


def _compute_skill_set_hash(skill_names: Iterable[str]) -> str:
    """Deterministic short hash of the current skill name list.

    Uses sorted names so add/remove of a single skill produces a
    different hash but reordering the internal list (which Python
    doesn't do but future refactors might) does not.
    """
    joined = "\n".join(sorted(skill_names))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _query_key(user_message: str) -> str:
    """Cache key for the user's message. Matches the in-memory
    cache key convention in ``skill_router.py`` (first 200 chars).
    """
    return user_message[:200]


def _read_file() -> dict[str, Any]:
    """Load the raw cache file. Returns an empty dict on missing
    or corrupt file."""
    if not _CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        _log.debug("skill_router_cache read failed: %s", exc)
    return {}


def _write_file(data: dict[str, Any]) -> None:
    """Atomic write via tmp-file-and-rename. Best-effort: failures
    log at DEBUG and return silently."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(_CACHE_PATH.parent),
            prefix=".skill_router_cache.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, _CACHE_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        _log.debug("skill_router_cache write failed: %s", exc)


def load_cached_match(
    user_message: str,
    skill_names: Iterable[str],
) -> Any:
    """Look up a previous Tier C answer for ``user_message``.

    Returns:
        - ``NOT_CACHED`` sentinel: no entry for this query+skill-set
        - ``None``: cached "no skill matched" — skip Tier C, return []
        - ``str`` (a skill name): cached positive — caller should
          look up the skill by name in its current skill list

    Callers must handle the case where the cached skill name no
    longer exists in the current skill list (e.g. user removed a
    skill file between sessions) — treat that as NOT_CACHED.
    """
    data = _read_file()
    hash_key = _compute_skill_set_hash(skill_names)
    bucket = data.get(hash_key)
    if not isinstance(bucket, dict):
        return NOT_CACHED
    entries = bucket.get("entries")
    if not isinstance(entries, dict):
        return NOT_CACHED
    entry = entries.get(_query_key(user_message))
    if not isinstance(entry, dict):
        return NOT_CACHED
    if "skill" not in entry:
        return NOT_CACHED
    return entry["skill"]


def save_match(
    user_message: str,
    skill_names: Iterable[str],
    matched_skill: str | None,
) -> None:
    """Record a Tier C result.

    ``matched_skill=None`` caches a negative result (no skill
    matched). ``matched_skill=<name>`` caches a positive result.

    Automatically prunes the bucket to ``_MAX_ENTRIES`` by dropping
    the oldest entries (by ``cached_at``) when the cap is reached.
    """
    hash_key = _compute_skill_set_hash(skill_names)
    query_key = _query_key(user_message)
    now = datetime.now(timezone.utc).isoformat()

    data = _read_file()
    bucket = data.get(hash_key)
    if not isinstance(bucket, dict):
        bucket = {"entries": {}}
        data[hash_key] = bucket
    entries = bucket.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        bucket["entries"] = entries

    entries[query_key] = {
        "skill": matched_skill,
        "cached_at": now,
    }

    # Prune: if over cap, drop oldest by cached_at
    if len(entries) > _MAX_ENTRIES:
        sorted_items = sorted(
            entries.items(),
            key=lambda kv: kv[1].get("cached_at", "") if isinstance(kv[1], dict) else "",
        )
        # Keep the newest _MAX_ENTRIES
        keep = dict(sorted_items[-_MAX_ENTRIES:])
        bucket["entries"] = keep

    _write_file(data)


def clear_cache(
    skill_set_hash: str | None = None,
) -> None:
    """Clear the cache. No arg = full wipe. With a specific
    ``skill_set_hash`` = drop only that bucket.

    Exposed for tests and for a future ``/cache clear`` user command.
    """
    if skill_set_hash is None:
        try:
            _CACHE_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    data = _read_file()
    if skill_set_hash in data:
        del data[skill_set_hash]
        _write_file(data)

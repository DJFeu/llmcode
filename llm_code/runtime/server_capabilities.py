"""Persistent cache of per-server capability probe results.

When a new server+model combination hits the ``conversation.py``
auto-fallback branch (``"Server does not support native tool
calling; falling back to XML tag mode"``), we write the result
here so the NEXT session for the same server skips native mode
entirely and saves the ~14s the native-rejection round-trip
takes.

Keyed by ``(base_url, model)`` so a user running several vLLM
servers with different configs, or switching models on the same
server, gets independent cache entries. Values are:

    {"native_tools": false, "cached_at": "2026-04-09T13:30:00Z"}

File format is a tiny JSON object written atomically via
tmp-file-and-rename so a concurrent reader never sees a partial
write. Failures to read or write the cache are swallowed — this
is a pure optimization, not a correctness boundary, so a missing
or corrupted cache just means the user pays the 14s fallback one
more time.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Cache file location. Lives next to the existing conversations.db
# and checkpoints directory so a user who wants a clean slate can
# just ``rm -rf ~/.llmcode/`` and get one.
_CACHE_PATH = Path.home() / ".llmcode" / "server_capabilities.json"

# Entries older than this are treated as expired and re-probed.
# 7 days is long enough to avoid re-probing every session but
# short enough that a vLLM upgrade is discovered within a week.
_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _cache_key(base_url: str, model: str) -> str:
    """The cache is keyed by the exact base_url + model combo.

    base_url may contain a trailing slash or not; normalize by
    stripping trailing slashes. Model name is used as-is since
    different paths (e.g. ``/models/Qwen3.5-122B`` vs
    ``/models/Qwen3.5-122B-A10B-int4-AutoRound``) are distinct
    capabilities.
    """
    return f"{base_url.rstrip('/')}|{model}"


def load_native_tools_support(base_url: str, model: str) -> bool | None:
    """Return the cached ``native_tools`` support flag, or None if
    the combo has never been probed (or the cache is unreadable).

    A None return means "don't know, go ahead and try native and
    let the fallback branch discover the answer". Explicit False
    means "already tried this server, skip native mode entirely".
    """
    if not _CACHE_PATH.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    entry = data.get(_cache_key(base_url, model))
    if not isinstance(entry, dict):
        return None
    # TTL check: entries older than 7 days are treated as expired
    # so a server that was upgraded (e.g. vLLM got
    # --enable-auto-tool-choice) will be re-probed automatically
    # without the user having to manually clear the cache.
    cached_at = entry.get("cached_at")
    if isinstance(cached_at, str):
        try:
            from datetime import datetime, timezone
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)
            if age.total_seconds() > _TTL_SECONDS:
                _log.debug(
                    "server_capabilities cache expired for %s (%s): "
                    "age %.0f seconds > TTL %d",
                    base_url, model, age.total_seconds(), _TTL_SECONDS,
                )
                return None
        except (ValueError, TypeError):
            pass  # Malformed timestamp — treat as valid (don't expire)
    value = entry.get("native_tools")
    if isinstance(value, bool):
        return value
    return None


def mark_native_tools_unsupported(base_url: str, model: str) -> None:
    """Record that this server+model combo does NOT support native
    tool calling so the next session's first turn skips the 14s
    native-rejection round-trip.

    Writes atomically: serialize to a tmp file in the same
    directory, then ``os.replace()`` into place. Any exception
    during write is logged at DEBUG and swallowed — a failed
    cache write is never worth failing the turn over.
    """
    key = _cache_key(base_url, model)
    now = datetime.now(timezone.utc).isoformat()

    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Load existing (merge, don't clobber other entries)
        if _CACHE_PATH.exists():
            try:
                data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}

        data[key] = {"native_tools": False, "cached_at": now}

        # Atomic write: tmp file in same directory, then replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(_CACHE_PATH.parent),
            prefix=".server_capabilities.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, _CACHE_PATH)
        except Exception:
            # Clean up tmp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        _log.debug("server_capabilities cache write failed: %s", exc)


def clear_native_tools_cache(base_url: str | None = None, model: str | None = None) -> None:
    """Clear the cache — full wipe by default, or a single entry
    when base_url+model are both provided. Exposed for tests and
    for a future ``/cache clear`` user command."""
    if base_url is None and model is None:
        try:
            _CACHE_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    if base_url is None or model is None:
        raise ValueError("clear_native_tools_cache requires both or neither")
    if not _CACHE_PATH.exists():
        return
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop(_cache_key(base_url, model), None)
            _CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass

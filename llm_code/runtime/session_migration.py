"""Session schema migration with tolerant loading.

When resuming a session from disk we want to gracefully handle older
message schemas — migrate old field names, drop orphan thinking-only
messages, rebuild tool-use chains, and tolerate unknown fields rather
than crashing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from llm_code.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 3
SCHEMA_VERSION_KEY = "_schema_version"


# ---------------------------------------------------------------------------
# Per-version migrations
# ---------------------------------------------------------------------------

def _migrate_v1_to_v2(msg: dict) -> dict:
    """v1 used 'attachments', v2 uses 'images'."""
    out = dict(msg)
    if "attachments" in out and "images" not in out:
        out["images"] = out.pop("attachments")
    return out


def _migrate_v2_to_v3(msg: dict) -> dict:
    """v2 used 'tool_calls' on assistant messages, v3 inlines blocks in 'content'."""
    out = dict(msg)
    if "tool_calls" in out:
        calls = out.pop("tool_calls") or []
        content = list(out.get("content") or [])
        for call in calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "input": call.get("input", {}),
                }
            )
        out["content"] = content
    return out


MIGRATIONS: dict[tuple[int, int], Callable[[dict], dict]] = {
    (1, 2): _migrate_v1_to_v2,
    (2, 3): _migrate_v2_to_v3,
}


def migrate_message(msg: dict, from_version: int) -> dict:
    """Apply migrations in sequence to bring an old message to current schema."""
    current = msg
    v = from_version
    while v < SCHEMA_VERSION:
        step = MIGRATIONS.get((v, v + 1))
        if step is None:
            logger.warning("No migration from v%d->v%d, leaving as-is", v, v + 1)
            break
        current = step(current)
        v += 1
    return current


# ---------------------------------------------------------------------------
# Cleanup passes
# ---------------------------------------------------------------------------

def _has_text_or_tool(content: list) -> bool:
    for block in content or ():
        btype = block.get("type") if isinstance(block, dict) else None
        if btype in ("text", "tool_use", "tool_result", "image"):
            return True
    return False


def filter_orphan_thinking(messages: list[dict]) -> list[dict]:
    """Drop thinking-only messages with no following text/tool block."""
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content") or []
        only_thinking = bool(content) and all(
            isinstance(b, dict) and b.get("type") == "thinking" for b in content
        )
        if only_thinking and not _has_text_or_tool(content):
            continue
        out.append(msg)
    return out


def rebuild_tool_chain(messages: list[dict]) -> list[dict]:
    """Drop tool_use blocks lacking a matching tool_result and vice versa."""
    seen_tool_use_ids: set[str] = set()
    seen_tool_result_ids: set[str] = set()
    for msg in messages:
        for block in msg.get("content") or ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                seen_tool_use_ids.add(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                seen_tool_result_ids.add(block["tool_use_id"])

    valid_ids = seen_tool_use_ids & seen_tool_result_ids

    out: list[dict] = []
    for msg in messages:
        new_content = []
        for block in msg.get("content") or ():
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            btype = block.get("type")
            if btype == "tool_use":
                if block.get("id") in valid_ids:
                    new_content.append(block)
            elif btype == "tool_result":
                if block.get("tool_use_id") in valid_ids:
                    new_content.append(block)
            else:
                new_content.append(block)
        new_msg = dict(msg)
        new_msg["content"] = new_content
        out.append(new_msg)
    return out


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

# Fields the loader recognises on a message — anything else is dropped with a
# warning rather than crashing.
KNOWN_MESSAGE_FIELDS = {"role", "content", "images", "metadata", "timestamp"}


def _strip_unknown_fields(msg: dict) -> dict:
    out = {}
    for k, v in msg.items():
        if k in KNOWN_MESSAGE_FIELDS:
            out[k] = v
        else:
            logger.debug("Dropping unknown message field: %s", k)
    return out


def load_and_migrate(session_path: Path) -> list[dict]:
    """Read a session.json file, detect schema version, migrate, validate."""
    raw = json.loads(Path(session_path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        version = 1
        messages = raw
    else:
        version = int(raw.get(SCHEMA_VERSION_KEY, 1))
        messages = raw.get("messages") or []

    migrated = [migrate_message(m, version) for m in messages]
    cleaned = filter_orphan_thinking(migrated)
    chained = rebuild_tool_chain(cleaned)
    return [_strip_unknown_fields(m) for m in chained]

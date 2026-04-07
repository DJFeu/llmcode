"""Versioned config migration system — run-once, idempotent.

Migrations transform user config dicts as defaults evolve across versions.
Each migration runs once; state tracked in ~/.llmcode/migration-state.json.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_STATE_FILE = "migration-state.json"


@dataclass(frozen=True)
class Migration:
    """A single config migration."""

    version: str
    description: str
    migrate: Callable[[dict], dict]


def _migration_1_1_0_001(cfg: dict) -> dict:
    """Add config_version field if missing."""
    if "config_version" not in cfg:
        cfg["config_version"] = "1.1.0"
    return cfg


def _migration_1_1_0_002(cfg: dict) -> dict:
    """Add skill_router defaults if missing."""
    if "skill_router" not in cfg:
        cfg["skill_router"] = {
            "enabled": True,
            "tier_a": True,
            "tier_b": True,
            "tier_c": False,
        }
    return cfg


def _migration_1_1_0_003(cfg: dict) -> dict:
    """Add diminishing_returns defaults if missing."""
    if "diminishing_returns" not in cfg:
        cfg["diminishing_returns"] = {
            "enabled": True,
            "min_continuations": 3,
            "min_delta_tokens": 500,
        }
    return cfg


_DEPRECATED_MODEL_MAP: dict[str, str] = {
    "claude-3-opus-20240229": "claude-opus-4-5",
    "claude-3-sonnet-20240229": "claude-sonnet-4-5",
    "claude-3-haiku-20240307": "claude-haiku-4-5",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
    "qwen2.5-coder-32b": "qwen3-coder-32b",
    "qwen2.5-72b-instruct": "qwen3-122b-instruct",
    "qwen-2.5-coder": "qwen3-coder-32b",
}


def _rewrite_model(value: object) -> tuple[object, bool]:
    """Return (new_value, changed) — only rewrites strings in the map."""
    if isinstance(value, str) and value in _DEPRECATED_MODEL_MAP:
        return _DEPRECATED_MODEL_MAP[value], True
    return value, False


def _migration_1_1_0_004_model_upgrade(cfg: dict) -> dict:
    """Rewrite deprecated model IDs to current ones across known fields."""
    log: dict[str, str] = {}

    new_model, changed = _rewrite_model(cfg.get("model"))
    if changed:
        log["model"] = cfg["model"]  # type: ignore[assignment]
        cfg["model"] = new_model
        logger.info("Migrated deprecated model %s -> %s", log["model"], new_model)

    subagent = cfg.get("subagent")
    if isinstance(subagent, dict) and "model" in subagent:
        new_v, changed = _rewrite_model(subagent["model"])
        if changed:
            log["subagent.model"] = subagent["model"]
            subagent["model"] = new_v
            logger.info("Migrated subagent.model %s -> %s", log["subagent.model"], new_v)

    routing = cfg.get("model_routing")
    if isinstance(routing, dict):
        for key in ("sub_agent", "compaction", "fallback"):
            if key in routing:
                new_v, changed = _rewrite_model(routing[key])
                if changed:
                    log[f"model_routing.{key}"] = routing[key]
                    routing[key] = new_v
                    logger.info(
                        "Migrated model_routing.%s %s -> %s",
                        key,
                        log[f"model_routing.{key}"],
                        new_v,
                    )

    knowledge = cfg.get("knowledge")
    if isinstance(knowledge, dict) and "compile_model" in knowledge:
        new_v, changed = _rewrite_model(knowledge["compile_model"])
        if changed:
            log["knowledge.compile_model"] = knowledge["compile_model"]
            knowledge["compile_model"] = new_v

    skill_router = cfg.get("skill_router")
    if isinstance(skill_router, dict) and "tier_c_model" in skill_router:
        new_v, changed = _rewrite_model(skill_router["tier_c_model"])
        if changed:
            log["skill_router.tier_c_model"] = skill_router["tier_c_model"]
            skill_router["tier_c_model"] = new_v

    if log:
        existing = cfg.get("_migration_log")
        if not isinstance(existing, dict):
            existing = {}
        existing.setdefault("1.1.0-004_model_upgrade", {}).update(log)
        cfg["_migration_log"] = existing

    return cfg


MIGRATION_REGISTRY: tuple[Migration, ...] = (
    Migration("1.1.0-001", "Add config_version field", _migration_1_1_0_001),
    Migration("1.1.0-002", "Add skill_router defaults", _migration_1_1_0_002),
    Migration("1.1.0-003", "Add diminishing_returns defaults", _migration_1_1_0_003),
    Migration("1.1.0-004", "Upgrade deprecated model IDs", _migration_1_1_0_004_model_upgrade),
)


def _read_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"applied": []}
    return {"applied": []}


def _write_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def apply_pending_migrations(
    config: dict,
    config_dir: Path | None = None,
) -> dict:
    """Apply all pending migrations to a config dict.

    Args:
        config: Mutable config dict (will be modified in place and returned).
        config_dir: Directory for migration-state.json (default: ~/.llmcode).

    Returns:
        The (possibly modified) config dict.
    """
    if config_dir is None:
        config_dir = Path.home() / ".llmcode"
    state_path = config_dir / _STATE_FILE
    state = _read_state(state_path)
    applied: set[str] = set(state.get("applied", []))

    changed = False
    for migration in MIGRATION_REGISTRY:
        if migration.version in applied:
            continue
        try:
            config = migration.migrate(config)
            applied.add(migration.version)
            changed = True
            logger.info("Applied config migration %s: %s", migration.version, migration.description)
        except Exception:
            logger.warning("Config migration %s failed, skipping", migration.version, exc_info=True)

    if changed:
        state["applied"] = sorted(applied)
        _write_state(state_path, state)

    return config

"""Config schema versioning (M6).

Callers that load JSON/TOML config from disk pipe the dict through
:func:`migrate` so older schema versions get upgraded before the
runtime tries to parse them into dataclasses.
"""
from __future__ import annotations

from typing import Callable


CURRENT_SCHEMA_VERSION = 2

# (from_version, to_version) -> callable(cfg) -> cfg
_MIGRATORS: dict[tuple[int, int], Callable[[dict], dict]] = {}


def register_migrator(
    from_version: int,
    to_version: int,
    migrator: Callable[[dict], dict],
) -> None:
    """Register a ``from_version → to_version`` migration function."""
    _MIGRATORS[(from_version, to_version)] = migrator


def add_schema_version(cfg: dict) -> dict:
    """Stamp ``cfg`` with the current schema version when missing."""
    if "_schema_version" not in cfg:
        cfg["_schema_version"] = CURRENT_SCHEMA_VERSION
    return cfg


def migrate(cfg: dict) -> dict:
    """Upgrade ``cfg`` to :data:`CURRENT_SCHEMA_VERSION`.

    Newer-than-current configs are left untouched so the caller can
    surface them to the user rather than corrupt them.
    """
    version = int(cfg.get("_schema_version", CURRENT_SCHEMA_VERSION))
    if version >= CURRENT_SCHEMA_VERSION:
        return cfg
    if version < 1:
        raise RuntimeError(
            f"no migration path for schema version {version}"
        )
    current = cfg
    while version < CURRENT_SCHEMA_VERSION:
        step = _MIGRATORS.get((version, version + 1))
        if step is None:
            raise RuntimeError(
                f"no migration path for schema version "
                f"{version} → {version + 1}"
            )
        current = step(current)
        version = int(current.get("_schema_version", version + 1))
    return current

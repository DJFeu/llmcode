"""M6: config schema versioning + migration."""
from __future__ import annotations

import pytest

from llm_code.runtime.config_version import (
    CURRENT_SCHEMA_VERSION,
    add_schema_version,
    migrate,
    register_migrator,
)


class TestAddSchemaVersion:
    def test_adds_missing_version(self) -> None:
        out = add_schema_version({"foo": 1})
        assert out["_schema_version"] == CURRENT_SCHEMA_VERSION

    def test_preserves_explicit_version(self) -> None:
        out = add_schema_version({"_schema_version": 1, "foo": 1})
        assert out["_schema_version"] == 1


class TestMigrate:
    def test_current_version_passthrough(self) -> None:
        cfg = {"_schema_version": CURRENT_SCHEMA_VERSION, "foo": 1}
        out = migrate(cfg)
        assert out == cfg

    def test_future_version_left_alone(self) -> None:
        """Never downgrade — if someone hands us a newer version we
        don't know how to deal with, leave it unchanged and let the
        caller decide."""
        cfg = {"_schema_version": CURRENT_SCHEMA_VERSION + 5}
        out = migrate(cfg)
        assert out["_schema_version"] == CURRENT_SCHEMA_VERSION + 5

    def test_migrator_chain_runs_in_order(self) -> None:
        # Register a fake migrator from v1 → v2.
        calls: list[int] = []

        def v1_to_v2(cfg: dict) -> dict:
            calls.append(1)
            cfg["migrated_v1_to_v2"] = True
            cfg["_schema_version"] = 2
            return cfg

        register_migrator(1, 2, v1_to_v2)
        try:
            out = migrate({"_schema_version": 1})
        finally:
            from llm_code.runtime.config_version import _MIGRATORS
            _MIGRATORS.pop((1, 2), None)
        assert out["migrated_v1_to_v2"] is True
        assert calls == [1]

    def test_missing_migrator_raises(self) -> None:
        with pytest.raises(RuntimeError, match="migration path"):
            migrate({"_schema_version": -1})

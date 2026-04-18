"""Tests for the settings JSON Schema exporter (H9 — Sprint 3).

``export_settings_schema(dataclass)`` produces a JSON Schema dict
describing a dataclass tree. Purpose: generate ``schemas/settings.schema.json``
so IDEs (VS Code via ``$schema``) can validate user ``settings.json``
without duplicating the type info.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest

from llm_code.config.schema_export import (
    SCHEMA_VERSION,
    export_settings_schema,
    write_schema_file,
)


# ---------- Sample dataclasses ----------


class _Mode(Enum):
    FAST = "fast"
    SAFE = "safe"


@dataclass(frozen=True)
class _InnerCfg:
    retries: int = 3
    timeout: float = 5.0


@dataclass(frozen=True)
class _TopCfg:
    name: str
    mode: _Mode = _Mode.FAST
    enabled: bool = True
    tags: tuple[str, ...] = ()
    inner: _InnerCfg = field(default_factory=_InnerCfg)


# ---------- export_settings_schema ----------


class TestExportSettingsSchema:
    def test_top_level_shape(self) -> None:
        schema = export_settings_schema(_TopCfg)
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

    def test_required_fields_match_missing_defaults(self) -> None:
        """``name`` has no default — must be required.
        Every other field has a default and must NOT be required."""
        schema = export_settings_schema(_TopCfg)
        assert schema["required"] == ["name"]

    def test_primitive_types_mapped(self) -> None:
        schema = export_settings_schema(_TopCfg)
        props = schema["properties"]
        assert props["name"]["type"] == "string"
        assert props["enabled"]["type"] == "boolean"

    def test_tuple_maps_to_array(self) -> None:
        schema = export_settings_schema(_TopCfg)
        props = schema["properties"]
        assert props["tags"]["type"] == "array"
        assert props["tags"]["items"]["type"] == "string"

    def test_enum_mapped_to_string_enum(self) -> None:
        schema = export_settings_schema(_TopCfg)
        mode = schema["properties"]["mode"]
        assert mode["type"] == "string"
        assert sorted(mode["enum"]) == ["fast", "safe"]

    def test_nested_dataclass_expanded(self) -> None:
        schema = export_settings_schema(_TopCfg)
        inner = schema["properties"]["inner"]
        assert inner["type"] == "object"
        assert inner["properties"]["retries"]["type"] == "integer"
        assert inner["properties"]["timeout"]["type"] == "number"

    def test_version_embedded(self) -> None:
        schema = export_settings_schema(_TopCfg)
        assert schema["x-llmcode-schema-version"] == SCHEMA_VERSION


# ---------- write_schema_file ----------


class TestWriteSchemaFile:
    def test_pretty_printed_and_loadable(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "settings.schema.json"
        write_schema_file(_TopCfg, out)
        assert out.is_file()
        data = json.loads(out.read_text())
        # Same structure as direct export
        assert data == export_settings_schema(_TopCfg)
        # Pretty-printed (no single-line JSON)
        assert "\n" in out.read_text()

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "c" / "s.json"
        write_schema_file(_TopCfg, out)
        assert out.is_file()


# ---------- Edge cases ----------


class TestEdgeCases:
    def test_unknown_annotation_falls_back(self) -> None:
        """Exotic types (e.g. ``Any``) should not crash the exporter —
        they map to an unconstrained ``{}``."""

        @dataclass(frozen=True)
        class Weird:
            payload: object = None  # type: ignore[assignment]

        schema = export_settings_schema(Weird)
        # ``payload`` should appear even though we don't know how to
        # constrain it further.
        assert "payload" in schema["properties"]

    def test_rejects_non_dataclass(self) -> None:
        with pytest.raises(TypeError):
            export_settings_schema(int)  # type: ignore[arg-type]

"""Dataclass → JSON Schema exporter (H9 skeleton — Sprint 3).

The goal is ``schemas/settings.schema.json``: one authoritative
description of the settings file that VS Code (via ``$schema``) uses
to validate user config. We generate it from the existing
:class:`~llm_code.runtime.config.RuntimeConfig` dataclass tree so the
schema never drifts from the Python types.

Scope: handles the primitives that appear in llm-code configs today
(str, int, float, bool, tuple of primitives, Enum, nested dataclass,
Optional). Exotic types fall back to unconstrained ``{}`` entries.
Full coverage for every corner case (Union with >2 members,
``dict[str, T]``, generics) lands later if the schema ever grows to
need it.
"""
from __future__ import annotations

import dataclasses
import json
import types
from enum import Enum
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1
_SCHEMA_URL = "https://json-schema.org/draft-07/schema#"


def export_settings_schema(cls: type) -> dict[str, Any]:
    """Render ``cls`` (a dataclass) as a JSON Schema dict.

    Top-level schema carries ``$schema`` and our own
    ``x-llmcode-schema-version`` so readers can detect the format
    generation.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    body = _dataclass_schema(cls)
    body["$schema"] = _SCHEMA_URL
    body["x-llmcode-schema-version"] = SCHEMA_VERSION
    return body


def write_schema_file(cls: type, path: Path) -> None:
    """Write ``export_settings_schema(cls)`` to ``path`` pretty-printed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = export_settings_schema(cls)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# ── Internals ────────────────────────────────────────────────────────


def _dataclass_schema(cls: type) -> dict[str, Any]:
    hints = get_type_hints(cls)
    fields_map = {f.name: f for f in dataclasses.fields(cls)}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, hint in hints.items():
        f = fields_map.get(name)
        if f is None:
            continue
        properties[name] = _type_to_schema(hint)
        if (
            f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
        ):
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _type_to_schema(hint: Any) -> dict[str, Any]:
    # Unwrap Optional[X] → X | None
    origin = get_origin(hint)
    args = get_args(hint)

    # typing.Optional[T] shows up as Union[T, None]. PEP 604 ``T | None``
    # has origin ``types.UnionType``.
    if origin in (types.UnionType, getattr(types, "UnionType", None)) or (
        origin is not None and origin.__class__.__name__ == "SpecialForm"
    ):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])

    # Handle Union written via typing.Union (rare in new code).
    try:
        from typing import Union  # noqa: PLC0415

        if origin is Union:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                return _type_to_schema(non_none[0])
    except Exception:
        pass

    # tuple / list → array
    if origin in (tuple, list):
        inner = args[0] if args else Any
        return {"type": "array", "items": _type_to_schema(inner)}

    # Enum → string enum
    if isinstance(hint, type) and issubclass(hint, Enum):
        return {"type": "string", "enum": sorted(m.value for m in hint)}

    # Nested dataclass → inline sub-object
    if dataclasses.is_dataclass(hint):
        return _dataclass_schema(hint)

    # Primitive mapping
    if hint is str:
        return {"type": "string"}
    if hint is bool:
        # bool must come before int because ``issubclass(bool, int)``
        return {"type": "boolean"}
    if hint is int:
        return {"type": "integer"}
    if hint is float:
        return {"type": "number"}

    # Fall back to unconstrained schema.
    return {}

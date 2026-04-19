"""Tool-schema filter for XML-mode models (M8).

Qwen3-Coder OSS running XML tool-call mode chokes on complex JSON
schemas (anyOf / oneOf / nested refs). When ``force_xml_tools=True``
on the model profile, the prompt builder first runs tool definitions
through :func:`filter_schemas_for_xml_mode` to drop the ones whose
schema is too complex to render as an XML example.

Native callers (Claude, Qwen cloud) skip this entirely via
:func:`filter_schemas_native`.
"""
from __future__ import annotations


def _schema_is_complex(schema: dict) -> bool:
    params = schema.get("parameters") or {}
    # anyOf / oneOf / allOf are the classic JSON-Schema combinators
    # that don't translate cleanly into a single XML-tag example.
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in params:
            return True
    return False


def filter_schemas_for_xml_mode(
    schemas: list[dict],
) -> list[dict]:
    """Keep only tools whose schema is simple enough for XML prompting."""
    return [s for s in schemas if not _schema_is_complex(s)]


def filter_schemas_native(schemas: list[dict]) -> list[dict]:
    """No-op for native tool-calling models — returns ``schemas`` intact."""
    return schemas

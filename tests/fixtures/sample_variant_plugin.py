"""Sample plugin module used by the parser variant loader tests.

Exports three attributes that the plugin-loader tests import via
``tests.fixtures.sample_variant_plugin:<name>`` dotted paths:

- ``SampleVariant`` — a valid ``ParserVariant`` instance for the
  happy-path test.
- ``NotAVariant`` — a plain object that isn't a ``ParserVariant``,
  used by the type-check test.
- ``_private_marker`` — referenced only indirectly; presence
  confirms normal attribute access works.
"""
from __future__ import annotations

import uuid

from llm_code.tools.parser_variants import ParserVariant
from llm_code.tools.parsing import ParsedToolCall


def _match_sample(raw: str) -> bool:
    return raw.startswith("SAMPLE:")


def _parse_sample(raw: str) -> ParsedToolCall | None:
    """Parse ``SAMPLE:NAME:ARGS_JSON`` — trivial body used only by
    the plugin loader tests. The real parser contract is tested
    against the built-in variants in the main suite."""
    if not raw.startswith("SAMPLE:"):
        return None
    rest = raw[len("SAMPLE:") :]
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    name = parts[0].strip()
    if not name:
        return None
    # The args payload is intentionally simple — a literal string
    # wrapped in a dict — so the fixture doesn't depend on JSON.
    return ParsedToolCall(
        id=str(uuid.uuid4()),
        name=name,
        args={"payload": parts[1]},
        source="xml_tag",
    )


SampleVariant = ParserVariant(
    name="sample_plugin",
    match=_match_sample,
    parse=_parse_sample,
)


# Non-variant object — used to test the isinstance guard in
# ``load_plugin_variant``.
NotAVariant = object()


# Presence marker — not a variant, just checks attribute access
# traverses to arbitrary names successfully.
_private_marker = "ok"

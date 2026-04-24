"""v13 Phase B parity gate — parser variant order.

Reads the pre-Phase-B parser snapshot and asserts the registry-driven
``parse_tool_calls(body, None, profile=<DEFAULT_VARIANT_ORDER>)``
produces the same ``ParsedToolCall`` shape (modulo the random uuid)
for every body in the corpus.

The corpus itself lives in ``scripts/capture_prompt_baseline.py`` and
covers every variant in ``DEFAULT_VARIANT_ORDER`` — both happy paths
and known-no-parse edge cases. Phase C deletes this file plus the
fixture once mainline parser tests exercise the same path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.tools.parser_variants import DEFAULT_VARIANT_ORDER
from llm_code.tools.parsing import parse_tool_calls

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "pre_v13_parser_snapshot.json"
)


def _load_snapshot() -> dict[str, list[dict]]:
    if not _FIXTURE.is_file():
        pytest.skip(
            "pre_v13_parser_snapshot.json missing — run "
            "scripts/capture_prompt_baseline.py first."
        )
    return json.loads(_FIXTURE.read_text())


_SNAPSHOT = _load_snapshot()


# Re-import the corpus from the capture script so the test data lives
# in exactly one place. The script defines it as a module-level list
# of (label, body) tuples.
def _load_corpus() -> list[tuple[str, str]]:
    import importlib
    import sys

    repo = Path(__file__).resolve().parents[3]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    mod = importlib.import_module("scripts.capture_prompt_baseline")
    return mod.PARSER_CORPUS


_CORPUS = _load_corpus()


class _ProfileWithDefaultOrder:
    """Minimal profile stand-in; only ``parser_variants`` is read."""

    parser_variants = DEFAULT_VARIANT_ORDER


@pytest.mark.parametrize("label,body", _CORPUS, ids=[label for label, _ in _CORPUS])
def test_parser_variant_order_unchanged(label: str, body: str) -> None:
    """Phase B → C must not change which calls parse out of any body
    when the profile declares the historical variant order."""
    expected = _SNAPSHOT[label]
    parsed = parse_tool_calls(body, None, profile=_ProfileWithDefaultOrder())
    actual = [
        {"name": p.name, "args": p.args, "source": p.source} for p in parsed
    ]
    assert actual == expected, (
        f"parser drift for label={label!r}\n"
        f"  expected = {expected!r}\n"
        f"  actual   = {actual!r}"
    )

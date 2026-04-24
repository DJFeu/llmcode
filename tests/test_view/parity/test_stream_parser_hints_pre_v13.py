"""v13 Phase B parity gate — stream parser hints.

Reads the pre-Phase-B stream snapshot and asserts a fresh
``StreamParser()`` (with v2.2.5 GLM-friendly defaults captured at
baseline time) produces an identical event sequence for every chunked
body in the corpus.

The Phase B objective: profile-driven hints feed the same defaults
through the registry path. Phase C will delete this file together
with the GLM-specific defaults baked into ``StreamParser`` — at that
point, the GLM-specific hints flow through ``65-glm-5.1.toml``
``[parser_hints]`` instead of the class body.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.view.stream_parser import StreamParser

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "pre_v13_stream_snapshot.json"
)


def _load_snapshot() -> dict[str, list[dict]]:
    if not _FIXTURE.is_file():
        pytest.skip(
            "pre_v13_stream_snapshot.json missing — run "
            "scripts/capture_prompt_baseline.py first."
        )
    return json.loads(_FIXTURE.read_text())


_SNAPSHOT = _load_snapshot()


def _load_corpus() -> list[tuple[str, list[str]]]:
    import importlib
    import sys

    repo = Path(__file__).resolve().parents[3]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    mod = importlib.import_module("scripts.capture_prompt_baseline")
    return mod.STREAM_CORPUS


_CORPUS = _load_corpus()


def _serialize(events) -> list[dict]:
    out: list[dict] = []
    for e in events:
        item: dict = {"kind": e.kind.value}
        if e.text:
            item["text"] = e.text
        if e.tool_call is not None:
            item["tool_call"] = {
                "name": e.tool_call.name,
                "args": e.tool_call.args,
                "source": e.tool_call.source,
            }
        out.append(item)
    return out


@pytest.mark.parametrize(
    "label,chunks", _CORPUS, ids=[label for label, _ in _CORPUS]
)
def test_stream_parser_event_sequence_unchanged(
    label: str, chunks: list[str]
) -> None:
    """Default ``StreamParser()`` event sequence must match the
    pre-Phase-B baseline — Phase B keeps the GLM-friendly defaults
    in the class body; Phase C migrates them to the GLM TOML and
    flips the class defaults to no-op tuples."""
    expected = _SNAPSHOT[label]
    parser = StreamParser()
    events: list = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.flush())
    actual = _serialize(events)
    assert actual == expected, (
        f"stream drift for label={label!r}\n"
        f"  expected = {expected!r}\n"
        f"  actual   = {actual!r}"
    )

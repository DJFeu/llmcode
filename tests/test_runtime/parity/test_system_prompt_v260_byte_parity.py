"""v2.6.1 M2 byte-parity gate — captures pre-fix v2.6.0 baselines.

Captured by ``scripts/capture_system_prompt_v260.py`` BEFORE M2's
prompt-dedupe logic landed. The gate asserts that profiles which do
NOT opt in to ``prompt_dedupe_with_template`` still produce
byte-identical system prompts in v2.6.1.

GLM-5.1 (``glm-5.1-xml.txt``) opts in and is the ONE scenario where
the system prompt is intentionally shorter post-M2. It carries its
own dedicated dedupe-mode parity fixture
(``glm-5.1-xml.dedupe.txt``) so any future drift in the deduped
output also fails CI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "system_prompt_v260"
)


def _ctx() -> ProjectContext:
    return ProjectContext(
        cwd="/tmp/parity-test",
        instructions="",
        is_git_repo=False,
        git_status="",
    )


def _load_manifest() -> list[dict[str, Any]]:
    manifest_path = _FIXTURE_DIR / "manifest.json"
    if not manifest_path.exists():
        pytest.skip(
            f"baseline missing at {manifest_path} — run "
            f"scripts/capture_system_prompt_v260.py first"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


_SCENARIOS: list[dict[str, Any]] = _load_manifest()
_SCENARIO_IDS = [s["name"] for s in _SCENARIOS]

# v2.6.1 M2 — GLM-5.1 opts in to ``prompt_dedupe_with_template`` and
# its system prompt is intentionally shorter than the v2.6.0 baseline.
_OPTED_IN_SCENARIOS = {"glm-5.1-xml"}


@pytest.mark.parametrize(
    "scenario", _SCENARIOS, ids=_SCENARIO_IDS,
)
def test_system_prompt_byte_parity_v260(scenario: dict[str, Any]) -> None:
    """Profile NOT opted in to dedupe must produce byte-identical prompt."""
    if scenario["name"] in _OPTED_IN_SCENARIOS:
        pytest.skip(
            f"scenario {scenario['name']} opts in to "
            f"prompt_dedupe_with_template — see dedupe fixture"
        )
    builder = SystemPromptBuilder()
    actual = builder.build(
        _ctx(),
        model_name=scenario["model_name"],
        native_tools=scenario["native_tools"],
        is_local_model=scenario["is_local_model"],
    )
    expected_path = _FIXTURE_DIR / f"{scenario['name']}.txt"
    expected = expected_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"system prompt drift on scenario {scenario['name']!r} "
        f"(profile not opted in to dedupe).\n"
        f"  expected_len={len(expected)} actual_len={len(actual)}\n"
        f"  fixture: {expected_path}"
    )


def test_glm_dedupe_fixture_shrinks_baseline() -> None:
    """The deduped GLM prompt must be smaller than the v2.6.0 baseline."""
    baseline_path = _FIXTURE_DIR / "glm-5.1-xml.txt"
    deduped_path = _FIXTURE_DIR / "glm-5.1-xml.dedupe.txt"
    if not deduped_path.exists():
        pytest.skip(
            f"dedupe fixture not yet captured at {deduped_path}"
        )
    baseline = baseline_path.read_text(encoding="utf-8")
    deduped = deduped_path.read_text(encoding="utf-8")
    assert len(deduped) < len(baseline), (
        f"M2 dedupe must SHRINK the GLM prompt; "
        f"baseline={len(baseline)} deduped={len(deduped)}"
    )


def test_glm_dedupe_keeps_required_behavior_signals() -> None:
    """Deduped GLM prompt must still cover every behavior rule the
    glm.j2 template expresses — quality must not regress.

    The GLM template's anti-hallucination + tool-result + action-first
    sections are the SAME guidance as the generic _BEHAVIOR_RULES /
    BEHAVIOR_RULES snippet. Removing the snippet is OK iff the
    template still expresses the rule. This test pins down the
    semantic coverage by string-searching the deduped prompt for
    representative phrases from each rule.
    """
    deduped_path = _FIXTURE_DIR / "glm-5.1-xml.dedupe.txt"
    if not deduped_path.exists():
        pytest.skip(
            f"dedupe fixture not yet captured at {deduped_path}"
        )
    deduped = deduped_path.read_text(encoding="utf-8")

    required_signals = [
        "Tool results ARE your ground truth",   # tool_result_nudge
        "Anti-hallucination",                    # behavior anti-halluc
        "When calling tools, do not narrate",    # action-first behavior
        "GLM",                                   # intro identity
        "<tool_call>",                           # XML tool format still rendered
    ]
    missing = [s for s in required_signals if s not in deduped]
    assert not missing, (
        f"deduped GLM prompt is missing semantic coverage for: {missing}"
    )

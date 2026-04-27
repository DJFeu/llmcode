"""Capture v2.6.0 system-prompt baselines for the M2 byte-parity gate.

Renders ``SystemPromptBuilder.build`` for a representative set of
profiles and writes the output to ``tests/fixtures/system_prompt_v260/``.
The M2 dedupe gate (``test_system_prompt_v260_byte_parity.py``) then
asserts that v2.6.1 still produces byte-identical output for every
profile that does NOT opt in to ``prompt_dedupe_with_template``.

Run BEFORE making M2 changes:

    python scripts/capture_system_prompt_v260.py

The fixtures are checked in so the parity gate runs on every CI run.
"""
from __future__ import annotations

import json
from pathlib import Path

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "system_prompt_v260"


def _ctx() -> ProjectContext:
    return ProjectContext(
        cwd="/tmp/parity-test",
        instructions="",
        is_git_repo=False,
        git_status="",
    )


# Each scenario captures (model_name, native_tools, is_local_model).
# Picked to span the three template families that absorb dedupe and
# the legacy paths that must stay byte-identical.
_SCENARIOS: list[dict[str, object]] = [
    {
        "name": "glm-5.1-xml",
        "model_name": "glm-5.1",
        "native_tools": False,
        "is_local_model": True,
    },
    {
        "name": "qwen3.5-122b-xml",
        "model_name": "qwen3.5-122b",
        "native_tools": False,
        "is_local_model": True,
    },
    {
        "name": "claude-opus-4-6-native",
        "model_name": "claude-opus-4-6",
        "native_tools": True,
        "is_local_model": False,
    },
    {
        "name": "deepseek-r1-xml",
        "model_name": "deepseek-r1",
        "native_tools": False,
        "is_local_model": False,
    },
    {
        "name": "default-no-model",
        "model_name": "",
        "native_tools": True,
        "is_local_model": False,
    },
]


def main() -> None:
    builder = SystemPromptBuilder()
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    for scenario in _SCENARIOS:
        prompt = builder.build(
            _ctx(),
            model_name=scenario["model_name"],  # type: ignore[arg-type]
            native_tools=scenario["native_tools"],  # type: ignore[arg-type]
            is_local_model=scenario["is_local_model"],  # type: ignore[arg-type]
        )
        path = _FIXTURE_DIR / f"{scenario['name']}.txt"
        path.write_text(prompt, encoding="utf-8")
        print(f"wrote {path} ({len(prompt)} chars)")

    # Also write a manifest so the gate test enumerates the same scenarios.
    manifest_path = _FIXTURE_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(_SCENARIOS, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()

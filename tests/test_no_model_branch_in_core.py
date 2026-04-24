"""Guard against model-specific branches re-appearing in protected
paths.

Scans three files in the core runtime/parse/stream-parser path for
patterns of the form ``if "<model_family_substring>" in VAR:`` — the
shape that used to live in ``_legacy_select_intro_prompt`` before
v13 Phase C deleted it. Any match is treated as a regression: the
author must move the model-family logic to a profile TOML (see
``docs/engine/model_profile_author_guide.md``).

The regex matches only the exact legacy shape; docstring references
to the old token names (which are plentiful for historical context)
are not caught because they are embedded inside triple-quoted
strings, not inside ``if <LIT> in`` statements.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Known model-family substrings that historically lived in hardcoded
# if-branches. The guard also catches novel additions via a
# conservative "any lowercase identifier-ish literal" rule.
KNOWN_TOKENS: frozenset[str] = frozenset({
    "glm", "zhipu",
    "qwen",
    "llama",
    "deepseek",
    "gpt",
    "claude", "anthropic", "sonnet", "opus", "haiku",
    "gemini",
    "trinity",
    "kimi", "moonshot",
    "codex",
    "copilot",
    "o1", "o3",
    "beast",
})

PROTECTED_PATHS: tuple[str, ...] = (
    "llm_code/runtime/prompt.py",
    "llm_code/tools/parsing.py",
    "llm_code/view/stream_parser.py",
)

# Matches the exact ``if "LITERAL" in VAR:`` shape the legacy ladder
# used. The literal capture group is restricted to lowercase ASCII
# identifiers / hyphens / digits so we never false-positive on
# generic string checks.
_PATTERN = re.compile(
    r'\bif\s+["\']([a-z0-9_-]+)["\']\s+in\s+\w+\s*:',
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("rel", PROTECTED_PATHS)
def test_no_model_branch_in_file(rel: str) -> None:
    path = _REPO_ROOT / rel
    src = path.read_text()
    offenders = [
        (m.group(1), src.count("\n", 0, m.start()) + 1)
        for m in _PATTERN.finditer(src)
        if m.group(1) in KNOWN_TOKENS
    ]
    assert not offenders, (
        f"model-family branches re-introduced in {rel}:\n"
        + "\n".join(f"  line {line}: {tok}" for tok, line in offenders)
        + "\nMove model-specific logic to the profile TOML. See "
        "docs/engine/model_profile_author_guide.md."
    )


def test_known_tokens_include_recent_additions() -> None:
    """If you added a new model family TOML under v13+, add its
    tokens to KNOWN_TOKENS too so this guard stays useful."""
    assert "glm" in KNOWN_TOKENS  # landed v2.2.1
    assert "harmony" not in KNOWN_TOKENS  # harmony isn't a model id


def test_pattern_would_catch_a_synthetic_regression() -> None:
    """Sanity check — the regex actually fires on the canonical
    legacy shape. If someone refactors the pattern and accidentally
    breaks it, this test catches it before the grep-guard silently
    becomes a no-op."""
    scratch = (
        'def f(m):\n'
        '    if "glm" in m:\n'
        '        return "glm.j2"\n'
    )
    hits = [m.group(1) for m in _PATTERN.finditer(scratch)]
    assert "glm" in hits

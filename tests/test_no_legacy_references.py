"""v12 M8.b grep smoke test.

Guarantees that no file under ``llm_code/`` or ``tests/`` (excluding this
file itself, the migrate codemod rewriters — which legitimately *search*
for the legacy symbols — the migrate test fixtures, and CHANGELOG /
docs files) references any of the v12 legacy sentinels:

* ``LegacyToolExecutionPipeline``  — compat class, deleted in M8.b
* ``LLMCODE_V12``                  — transitional env-var, deleted in M8.b
* ``_v12_enabled``                 — internal feature flag, deleted in M8.b
* ``runtime/prompts/mode/``        — legacy markdown template dir,
  deleted in M8.b (engine/prompts/modes/*.j2 replaces it).

Whitelisted files (they have to mention the symbols because they exist
to talk about the migration):

* ``tests/test_no_legacy_references.py``         — this file.
* ``llm_code/migrate/v12/rewriters/**``          — codemod source that
  pattern-matches the legacy import strings to *rewrite* them.
* ``tests/test_migrate/**``                      — codemod test suite.
* ``docs/breaking_changes_v2.md``                — documents the
  old -> new mapping.
* ``docs/upgrade_to_v2.md``                      — tells users which
  symbols were removed.
* ``docs/plugin_migration_guide.md``             — shows the
  before/after diffs.
* ``CHANGELOG.md``                               — chronicles the
  removal.

Any match outside that whitelist fails the test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

# Regex patterns (compiled once)
LEGACY_PATTERNS: dict[str, re.Pattern[str]] = {
    "LegacyToolExecutionPipeline": re.compile(r"\bLegacyToolExecutionPipeline\b"),
    "LLMCODE_V12": re.compile(r"\bLLMCODE_V12\b"),
    "_v12_enabled": re.compile(r"\b_v12_enabled\b"),
    "runtime_prompts_mode": re.compile(
        r"runtime[/.]prompts[/.]mode[/.]"
    ),
}

# Files / directories that are expected to mention these symbols.
WHITELIST: tuple[str, ...] = (
    # This file (self).
    "tests/test_no_legacy_references.py",
    # Migrate codemod — its job is to pattern-match legacy imports and
    # rewrite them.
    "llm_code/migrate/v12/rewriters/",
    # Migrate test suite — exercises the rewriters against before/after
    # fixtures that contain the legacy strings.
    "tests/test_migrate/",
    # Release documentation — documents what was removed.
    "docs/breaking_changes_v2.md",
    "docs/upgrade_to_v2.md",
    "docs/plugin_migration_guide.md",
    "CHANGELOG.md",
    # Superpowers design spec / plan files describe the migration and
    # naturally reference the legacy identifiers.
    "docs/superpowers/",
)

# Directories we scan.
SCAN_ROOTS: tuple[str, ...] = ("llm_code", "tests")

# Extensions we care about.
SCAN_EXTENSIONS: tuple[str, ...] = (".py", ".md", ".j2", ".toml")


def _is_whitelisted(rel_path: str) -> bool:
    for prefix in WHITELIST:
        if rel_path == prefix or rel_path.startswith(prefix):
            return True
    return False


def _collect_offences() -> list[tuple[str, str, int, str]]:
    """Return ``(symbol, rel_path, line_no, line)`` tuples for all
    offending grep matches outside the whitelist.
    """
    offences: list[tuple[str, str, int, str]] = []
    for root in SCAN_ROOTS:
        root_path = REPO_ROOT / root
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _is_whitelisted(rel):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for symbol, pattern in LEGACY_PATTERNS.items():
                for match_line_no, line in enumerate(
                    text.splitlines(), start=1
                ):
                    if pattern.search(line):
                        offences.append((symbol, rel, match_line_no, line))
    return offences


class TestNoLegacyReferences:
    def test_no_legacy_symbols_anywhere(self) -> None:
        offences = _collect_offences()
        if offences:
            formatted = "\n".join(
                f"  {symbol!s}: {rel}:{line_no}: {line.strip()}"
                for symbol, rel, line_no, line in offences[:40]
            )
            pytest.fail(
                f"Legacy symbol(s) leaked into non-whitelisted files "
                f"({len(offences)} matches, first 40 shown):\n"
                f"{formatted}"
            )

    def test_runtime_prompts_dir_does_not_exist(self) -> None:
        dead = REPO_ROOT / "llm_code" / "runtime" / "prompts"
        assert not dead.exists(), (
            f"Legacy prompt dir {dead} still on disk; M8.b should have "
            "deleted it (all model and mode templates now live in "
            "engine/prompts/)."
        )

    def test_parity_test_dir_does_not_exist(self) -> None:
        dead = REPO_ROOT / "tests" / "test_engine" / "parity"
        assert not dead.exists(), (
            f"Parity test directory {dead} still on disk; M8.b should "
            "have deleted it (legacy path is gone; nothing left to "
            "compare against)."
        )

    def test_engineconfig_v12_flag_is_gone(self) -> None:
        import dataclasses

        from llm_code.runtime.config import EngineConfig

        fields = {f.name for f in dataclasses.fields(EngineConfig)}
        assert "_v12_enabled" not in fields, (
            "EngineConfig still carries the transitional `_v12_enabled` "
            "field; M8.b should have deleted it."
        )

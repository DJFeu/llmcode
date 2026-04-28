"""Tests for ``llmcode profiles diff`` (v2.10.0 M4).

Covers the three states (missing / identical / diverged) plus error
paths (unknown profile name).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_code.cli import profiles_cmd
from llm_code.cli.profiles_cmd import profiles
from llm_code.profiles.builtins import builtin_profile_path


@pytest.fixture
def isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    fake = tmp_path / "user_profiles"
    fake.mkdir()
    monkeypatch.setattr(profiles_cmd, "_user_profile_dir", lambda: fake)
    return fake


def _glm_user_path(user_dir: Path) -> Path:
    return user_dir / "glm-5.1.toml"


def _glm_builtin_text() -> str:
    p = builtin_profile_path("glm-5.1")
    assert p is not None
    return p.read_text(encoding="utf-8")


# ── Missing user copy ────────────────────────────────────────────────


class TestDiffMissing:
    def test_missing_prints_helpful_message(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0, result.output
        assert "not installed" in result.output
        assert "llmcode profiles update glm-5.1" in result.output

    def test_missing_does_not_print_diff(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0
        # Diff markers should not be present.
        assert "+++" not in result.output
        assert "---" not in result.output


# ── Identical user copy ──────────────────────────────────────────────


class TestDiffIdentical:
    def test_identical_prints_nothing(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(_glm_builtin_text(), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0
        assert result.output.strip() == "", (
            f"expected empty output for identical files, "
            f"got: {result.output!r}"
        )


# ── Diverged user copy ────────────────────────────────────────────────


class TestDiffDiverged:
    def test_diverged_prints_unified_diff(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        # Add a single comment line at the end so the diff is small &
        # predictable.
        user_glm.write_text(
            _glm_builtin_text() + "\n# user override\n", encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0
        out = result.output
        # Unified diff header markers.
        assert "--- built-in/65-glm-5.1.toml" in out
        assert "+++ user/glm-5.1.toml" in out
        # The diverging line must be flagged.
        assert "+# user override" in out

    def test_diverged_includes_context_lines(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(
            _glm_builtin_text().replace(
                'compile_thinking_budget = 512',
                'compile_thinking_budget = 768',
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0
        out = result.output
        assert "-compile_thinking_budget = 512" in out
        assert "+compile_thinking_budget = 768" in out
        # Context lines (3 each side by default) ensure usability.
        assert out.count("@@") >= 1


# ── Argument validation ──────────────────────────────────────────────


class TestDiffArgValidation:
    def test_unknown_profile_errors(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "does-not-exist"])
        assert result.exit_code != 0
        assert "no built-in profile matches" in result.output

    def test_no_arg_errors(self, isolated_user_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["diff"])
        # Click exits with 2 when a required argument is missing.
        assert result.exit_code != 0


# ── Resolution helpers ───────────────────────────────────────────────


class TestDiffAcceptsBothNameForms:
    def test_full_stem(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(_glm_builtin_text() + "\n# x\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "65-glm-5.1"])
        assert result.exit_code == 0
        assert "+# x" in result.output

    def test_bare_slug(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(_glm_builtin_text() + "\n# y\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(profiles, ["diff", "glm-5.1"])
        assert result.exit_code == 0
        assert "+# y" in result.output

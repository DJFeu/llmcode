"""Tests for ``llmcode profiles list`` (v2.10.0 M2).

Each test points the helper module at a temporary user dir so the
real ``~/.llmcode/model_profiles/`` is never touched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_code.cli import profiles_cmd
from llm_code.cli.profiles_cmd import profiles
from llm_code.profiles.builtins import (
    builtin_profile_path,
    list_builtin_profile_paths,
    strip_numeric_prefix,
)


@pytest.fixture
def isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ``_user_profile_dir`` to a temp dir for each test."""
    fake = tmp_path / "user_profiles"
    fake.mkdir()
    monkeypatch.setattr(profiles_cmd, "_user_profile_dir", lambda: fake)
    return fake


def _user_path_for_slug(user_dir: Path, slug: str) -> Path:
    return user_dir / f"{slug}.toml"


class TestListBasic:
    def test_lists_every_bundled_profile_when_none_installed(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0, result.output

        for builtin in list_builtin_profile_paths():
            slug = strip_numeric_prefix(builtin.stem)
            assert slug in result.output

    def test_shows_not_installed_for_empty_user_dir(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        assert "not installed" in result.output

    def test_shows_user_and_builtin_dirs(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        assert "User profile dir:" in result.output
        assert str(isolated_user_dir) in result.output
        assert "Built-in profile dir:" in result.output

    def test_prints_version_in_header(
        self, isolated_user_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(profiles_cmd, "_resolve_version", lambda: "2.10.0")
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        assert "2.10.0" in result.output


class TestListStatusDetection:
    def test_marks_installed_when_byte_identical(
        self, isolated_user_dir: Path
    ) -> None:
        glm = builtin_profile_path("glm-5.1")
        assert glm is not None
        user_glm = _user_path_for_slug(isolated_user_dir, "glm-5.1")
        shutil.copy2(glm, user_glm)

        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        # Find the GLM line and assert it shows the installed marker.
        glm_line = next(
            line for line in result.output.splitlines()
            if line.lstrip().startswith("glm-5.1")
        )
        assert "installed (matches built-in)" in glm_line, glm_line

    def test_marks_diverged_when_user_copy_differs(
        self, isolated_user_dir: Path
    ) -> None:
        glm = builtin_profile_path("glm-5.1")
        assert glm is not None
        user_glm = _user_path_for_slug(isolated_user_dir, "glm-5.1")
        user_glm.write_text(
            glm.read_text(encoding="utf-8") + "\n# user edit\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        glm_line = next(
            line for line in result.output.splitlines()
            if line.lstrip().startswith("glm-5.1")
        )
        assert "DIVERGED" in glm_line, glm_line
        assert "llmcode profiles diff glm-5.1" in glm_line

    def test_mixed_states(self, isolated_user_dir: Path) -> None:
        # GLM identical, qwen3.5 diverged, claude-sonnet missing.
        glm = builtin_profile_path("glm-5.1")
        qwen = builtin_profile_path("qwen3.5-122b")
        assert glm is not None and qwen is not None

        shutil.copy2(glm, _user_path_for_slug(isolated_user_dir, "glm-5.1"))
        _user_path_for_slug(isolated_user_dir, "qwen3.5-122b").write_text(
            qwen.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        out = result.output
        assert "glm-5.1" in out and "matches built-in" in out
        assert "qwen3.5-122b" in out and "DIVERGED" in out
        # claude-sonnet is bundled but not installed.
        assert "claude-sonnet" in out and "not installed" in out


class TestListEdgeCases:
    def test_returns_zero_exit_code(self, isolated_user_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0

    def test_works_when_user_dir_does_not_exist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Point at a non-existent path; ``list`` must not crash.
        ghost = tmp_path / "does_not_exist"
        monkeypatch.setattr(profiles_cmd, "_user_profile_dir", lambda: ghost)
        runner = CliRunner()
        result = runner.invoke(profiles, ["list"])
        assert result.exit_code == 0
        assert "not installed" in result.output

"""Tests for ``llmcode profiles update`` (v2.10.0 M3).

Covers the behaviour matrix from the spec:

| User copy state | Default action |
|-----------------|----------------|
| Missing         | Copy bundled in |
| Identical       | Skip ("already up to date") |
| Diverged        | Show diff summary, prompt, backup, overwrite |

Plus the ``--force`` / ``--dry-run`` / ``--no-backup`` /
``--backup-suffix`` / ``--all`` flags.
"""
from __future__ import annotations

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
    fake = tmp_path / "user_profiles"
    fake.mkdir()
    monkeypatch.setattr(profiles_cmd, "_user_profile_dir", lambda: fake)
    return fake


def _glm_user_path(user_dir: Path) -> Path:
    return user_dir / "glm-5.1.toml"


def _glm_builtin() -> Path:
    p = builtin_profile_path("glm-5.1")
    assert p is not None
    return p


# ── Missing user copy ────────────────────────────────────────────────


class TestUpdateMissing:
    def test_creates_user_copy(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        assert not user_glm.exists()

        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "glm-5.1"])
        assert result.exit_code == 0, result.output
        assert user_glm.is_file()
        assert user_glm.read_bytes() == _glm_builtin().read_bytes()
        assert "installed" in result.output

    def test_dry_run_does_not_write(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert not user_glm.exists()
        assert "would create" in result.output


# ── Identical user copy ──────────────────────────────────────────────


class TestUpdateIdentical:
    def test_skips_with_message(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_bytes(_glm_builtin().read_bytes())
        original_mtime = user_glm.stat().st_mtime

        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "glm-5.1"])
        assert result.exit_code == 0, result.output
        assert "already up to date" in result.output
        # File untouched (mtime preserved).
        assert user_glm.stat().st_mtime == original_mtime


# ── Diverged user copy ────────────────────────────────────────────────


class TestUpdateDiverged:
    def test_force_overwrites_with_backup(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        diverged = _glm_builtin().read_text(encoding="utf-8") + "\n# user edit\n"
        user_glm.write_text(diverged, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1", "--force"]
        )
        assert result.exit_code == 0, result.output
        # Backup created.
        backups = list(isolated_user_dir.glob("glm-5.1.toml.bak*"))
        assert len(backups) == 1, f"expected 1 backup, got {backups}"
        assert backups[0].read_text(encoding="utf-8") == diverged
        # User copy now matches built-in.
        assert user_glm.read_bytes() == _glm_builtin().read_bytes()
        assert "backed up" in result.output
        assert "overwritten" in result.output

    def test_force_with_no_backup_skips_backup(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(
            _glm_builtin().read_text(encoding="utf-8") + "\n# edit\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1", "--force", "--no-backup"]
        )
        assert result.exit_code == 0, result.output
        backups = list(isolated_user_dir.glob("glm-5.1.toml.bak*"))
        assert backups == []
        assert "backed up" not in result.output
        assert user_glm.read_bytes() == _glm_builtin().read_bytes()

    def test_interactive_yes_overwrites(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(
            _glm_builtin().read_text(encoding="utf-8") + "\n# edit\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1"], input="y\n"
        )
        assert result.exit_code == 0, result.output
        assert "Overwrite" in result.output
        assert user_glm.read_bytes() == _glm_builtin().read_bytes()
        # Backup created by default.
        backups = list(isolated_user_dir.glob("glm-5.1.toml.bak*"))
        assert len(backups) == 1

    def test_interactive_no_skips(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        original_text = (
            _glm_builtin().read_text(encoding="utf-8") + "\n# edit\n"
        )
        user_glm.write_text(original_text, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1"], input="n\n"
        )
        assert result.exit_code == 0, result.output
        # User copy untouched.
        assert user_glm.read_text(encoding="utf-8") == original_text
        assert "skipped" in result.output
        # No backup written for a skipped update.
        backups = list(isolated_user_dir.glob("glm-5.1.toml.bak*"))
        assert backups == []

    def test_dry_run_shows_overwrite_plan(
        self, isolated_user_dir: Path
    ) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        original_text = (
            _glm_builtin().read_text(encoding="utf-8") + "\n# edit\n"
        )
        user_glm.write_text(original_text, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "would overwrite" in result.output
        # User copy untouched.
        assert user_glm.read_text(encoding="utf-8") == original_text
        # No backup written in dry-run mode.
        backups = list(isolated_user_dir.glob("glm-5.1.toml.bak*"))
        assert backups == []

    def test_custom_backup_suffix(self, isolated_user_dir: Path) -> None:
        user_glm = _glm_user_path(isolated_user_dir)
        user_glm.write_text(
            _glm_builtin().read_text(encoding="utf-8") + "\n# edit\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            profiles,
            [
                "update",
                "glm-5.1",
                "--force",
                "--backup-suffix",
                ".original",
            ],
        )
        assert result.exit_code == 0, result.output
        custom = isolated_user_dir / "glm-5.1.toml.original"
        assert custom.is_file()


# ── Update --all ─────────────────────────────────────────────────────


class TestUpdateAll:
    def test_creates_every_missing_profile(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "--all"])
        assert result.exit_code == 0, result.output

        for builtin in list_builtin_profile_paths():
            slug = strip_numeric_prefix(builtin.stem)
            user_path = isolated_user_dir / f"{slug}.toml"
            assert user_path.is_file(), f"missing {slug} after --all"
            assert user_path.read_bytes() == builtin.read_bytes()

    def test_all_dry_run_writes_nothing(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "--all", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        # Dry run must not have created any user files.
        assert list(isolated_user_dir.iterdir()) == []


# ── Argument validation ──────────────────────────────────────────────


class TestUpdateArgValidation:
    def test_no_args_errors(self, isolated_user_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["update"])
        assert result.exit_code != 0
        assert "Pass a profile name or --all" in (
            result.output or str(result.exception)
        )

    def test_name_and_all_together_errors(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "glm-5.1", "--all"]
        )
        assert result.exit_code != 0

    def test_unknown_profile_errors(
        self, isolated_user_dir: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            profiles, ["update", "does-not-exist"]
        )
        assert result.exit_code != 0
        assert "no built-in profile matches" in result.output


# ── Filesystem error path ────────────────────────────────────────────


class TestUpdateFilesystemErrors:
    def test_unwritable_user_dir_returns_nonzero(
        self,
        isolated_user_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force ``shutil.copy2`` to fail. The handler should report the
        # error to stderr/stdout and exit non-zero. ``monkeypatch``
        # restores the original after the test; we don't try to assert
        # global isolation here because the patched module attribute
        # IS the live shutil module.
        def _boom(*args: object, **kwargs: object) -> None:
            raise PermissionError("simulated denied")

        monkeypatch.setattr(profiles_cmd.shutil, "copy2", _boom)

        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "glm-5.1"])
        assert result.exit_code != 0
        combined = result.output + (
            (result.stderr_bytes or b"").decode("utf-8")
        )
        assert "error writing" in combined or "error overwriting" in combined


# ── Resolution helpers ───────────────────────────────────────────────


class TestUpdateAcceptsBothNameForms:
    def test_full_stem(self, isolated_user_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "65-glm-5.1"])
        assert result.exit_code == 0, result.output
        assert (isolated_user_dir / "glm-5.1.toml").is_file()

    def test_bare_slug(self, isolated_user_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(profiles, ["update", "glm-5.1"])
        assert result.exit_code == 0, result.output
        assert (isolated_user_dir / "glm-5.1.toml").is_file()

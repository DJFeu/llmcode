"""Tests for `llm_code.migrate.v12.runner._is_excluded`.

Regression-scope: when running the codemod against the llmcode repo
itself (self-migration sanity check), the tool must not rewrite its
own test fixtures or docs. The fixtures are intentionally written as
"legacy" plugin source for the before/after rewriter test suites.
"""
from __future__ import annotations

from pathlib import Path

from llm_code.migrate.v12.runner import (
    DEFAULT_EXCLUDED_PATH_PREFIXES,
    DEFAULT_GITIGNORE_PATTERNS,
    _is_excluded,
    _iter_python_files,
    _iter_pyproject_files,
)


class TestPathPrefixExclusions:
    def test_test_migrate_fixtures_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "tests" / "test_migrate" / "fixtures" / "legacy.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_test_migrate_fixtures_pyproject_excluded(
        self, tmp_path: Path
    ) -> None:
        target = (
            tmp_path
            / "tests"
            / "test_migrate"
            / "fixtures"
            / "plugin_a"
            / "pyproject.toml"
        )
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_tests_fixtures_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "tests" / "fixtures" / "runtime.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_tests_snapshots_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "tests" / "snapshots" / "baseline.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_docs_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "docs" / "example.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_real_source_not_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "plugin" / "main.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is False

    def test_real_test_not_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "tests" / "test_plugin_core.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is False

    def test_prefix_must_match_from_start(self, tmp_path: Path) -> None:
        # ``docs/`` prefix must not match ``src/docs.py``.
        target = tmp_path / "src" / "docs.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is False


class TestGitignorePatternExclusions:
    def test_venv_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / ".venv" / "lib" / "site.py"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_pycache_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / "pkg" / "__pycache__" / "module.cpython-313.pyc"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True

    def test_git_excluded(self, tmp_path: Path) -> None:
        target = tmp_path / ".git" / "hooks" / "post-commit"
        target.parent.mkdir(parents=True)
        target.touch()
        assert _is_excluded(target, tmp_path) is True


class TestConstantInvariants:
    def test_prefixes_end_with_slash(self) -> None:
        """Prefix excludes must end with ``/`` so ``docs/`` doesn't
        accidentally eat ``docstring.py``."""
        for prefix in DEFAULT_EXCLUDED_PATH_PREFIXES:
            assert prefix.endswith("/"), (
                f"prefix {prefix!r} missing trailing slash — would over-match"
            )

    def test_gitignore_patterns_non_empty(self) -> None:
        assert len(DEFAULT_GITIGNORE_PATTERNS) >= 5
        assert ".venv" in DEFAULT_GITIGNORE_PATTERNS
        assert "__pycache__" in DEFAULT_GITIGNORE_PATTERNS

    def test_excluded_prefixes_cover_self_fixtures(self) -> None:
        """The very reason this feature exists: codemod must skip its
        own test fixtures when run against llmcode itself."""
        assert any(
            "test_migrate/fixtures/" in p for p in DEFAULT_EXCLUDED_PATH_PREFIXES
        )


class TestIterationRespectsExclusions:
    def test_iter_python_skips_excluded_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "keep.py").write_text("x = 1\n")
        (tmp_path / "tests" / "test_migrate" / "fixtures").mkdir(parents=True)
        (tmp_path / "tests" / "test_migrate" / "fixtures" / "legacy.py").write_text(
            "# legacy fixture — MUST NOT be rewritten\n"
        )
        results = list(_iter_python_files(tmp_path))
        names = [p.name for p in results]
        assert "keep.py" in names
        assert "legacy.py" not in names

    def test_iter_pyproject_skips_fixture_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "real"\n'
        )
        (tmp_path / "tests" / "test_migrate" / "fixtures" / "legacy_plugin_a").mkdir(
            parents=True
        )
        (
            tmp_path
            / "tests"
            / "test_migrate"
            / "fixtures"
            / "legacy_plugin_a"
            / "pyproject.toml"
        ).write_text('[project]\nname = "legacy"\n')
        results = list(_iter_pyproject_files(tmp_path))
        paths = [str(p.relative_to(tmp_path)) for p in results]
        assert paths == ["pyproject.toml"], (
            f"expected only the root pyproject; got {paths}"
        )

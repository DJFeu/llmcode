"""Tests for :mod:`llm_code.migrate.v12.rewriters.pyproject_constraint`."""
from __future__ import annotations

import pytest

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters.pyproject_constraint import (
    V12_CONSTRAINT,
    rewrite_pyproject_source,
)


def _rewrite(source: str) -> tuple[str, Diagnostics]:
    diag = Diagnostics()
    new = rewrite_pyproject_source(source, "pyproject.toml", diag)
    return new, diag


class TestPEP621:
    def test_bumps_constraint_in_project_dependencies(self) -> None:
        src = (
            '[project]\n'
            'name = "x"\n'
            'dependencies = [\n'
            '    "llmcode>=1.20,<2.0",\n'
            '    "httpx>=0.27",\n'
            ']\n'
        )
        new, diag = _rewrite(src)
        assert f'"llmcode{V12_CONSTRAINT}"' in new
        assert '"httpx>=0.27"' in new
        assert not diag.any()

    def test_preserves_extras(self) -> None:
        src = (
            '[project]\n'
            'name = "x"\n'
            'dependencies = ["llmcode[anthropic]>=1.0"]\n'
        )
        new, _ = _rewrite(src)
        assert f'"llmcode[anthropic]{V12_CONSTRAINT}"' in new

    def test_preserves_env_marker(self) -> None:
        src = (
            '[project]\n'
            'name = "x"\n'
            'dependencies = [\'llmcode>=1.0 ; python_version < "3.11"\']\n'
        )
        new, _ = _rewrite(src)
        # The constraint itself is rewritten, marker is preserved.
        assert V12_CONSTRAINT in new
        # tomlkit may re-serialize the double-quoted marker with escapes;
        # either form preserves the semantic content.
        assert "python_version" in new and "3.11" in new

    def test_bumps_in_optional_dependencies(self) -> None:
        src = (
            '[project]\n'
            'name = "x"\n'
            'dependencies = []\n\n'
            '[project.optional-dependencies]\n'
            'plugin = ["llmcode>=1.0"]\n'
        )
        new, _ = _rewrite(src)
        assert f'"llmcode{V12_CONSTRAINT}"' in new


class TestPoetry:
    def test_bumps_simple_poetry_string(self) -> None:
        src = (
            '[tool.poetry]\n'
            'name = "plugin"\n'
            'version = "0.1.0"\n\n'
            '[tool.poetry.dependencies]\n'
            'python = "^3.10"\n'
            'llmcode = ">=1.20"\n'
        )
        new, _ = _rewrite(src)
        assert f'"{V12_CONSTRAINT}"' in new
        assert '"^3.10"' in new  # python line untouched

    def test_bumps_poetry_extended_table(self) -> None:
        src = (
            '[tool.poetry]\n'
            'name = "plugin"\n\n'
            '[tool.poetry.dependencies]\n'
            'llmcode = { version = ">=1.0", extras = ["anthropic"] }\n'
        )
        new, _ = _rewrite(src)
        assert V12_CONSTRAINT in new
        assert 'extras' in new

    def test_bumps_poetry_group_dep(self) -> None:
        src = (
            '[tool.poetry]\n'
            'name = "x"\n\n'
            '[tool.poetry.dependencies]\n\n'
            '[tool.poetry.group.dev.dependencies]\n'
            'llmcode = ">=1.0"\n'
        )
        new, _ = _rewrite(src)
        assert f'"{V12_CONSTRAINT}"' in new


class TestHatch:
    def test_bumps_hatch_env_dependencies(self) -> None:
        src = (
            '[project]\n'
            'name = "x"\n'
            'dependencies = []\n\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["llmcode>=1.0", "pytest>=8"]\n'
        )
        new, _ = _rewrite(src)
        assert f'"llmcode{V12_CONSTRAINT}"' in new
        assert '"pytest>=8"' in new


class TestIgnoredShapes:
    def test_no_llmcode_dep_returns_source_unchanged(self) -> None:
        src = '[project]\nname = "x"\ndependencies = ["httpx>=0.27"]\n'
        new, _ = _rewrite(src)
        assert new == src

    def test_already_v12_constraint_returns_source_unchanged(self) -> None:
        src = f'[project]\nname = "x"\ndependencies = ["llmcode{V12_CONSTRAINT}"]\n'
        new, _ = _rewrite(src)
        assert new == src

    def test_parse_error_reports_diagnostic(self) -> None:
        src = 'this is not toml [\n'
        new, diag = _rewrite(src)
        assert new == src
        patterns = {e.pattern for e in diag.entries}
        assert "pyproject_parse_error" in patterns

    def test_empty_file_is_noop(self) -> None:
        new, _ = _rewrite("")
        assert new == ""


@pytest.mark.parametrize(
    "constraint",
    [">=1.0", ">=1.20,<2.0", "~=1.5", "==1.22.0", "<2.0"],
)
def test_various_existing_constraints_are_bumped(constraint: str) -> None:
    src = f'[project]\nname = "x"\ndependencies = ["llmcode{constraint}"]\n'
    new, _ = _rewrite(src)
    assert f'"llmcode{V12_CONSTRAINT}"' in new

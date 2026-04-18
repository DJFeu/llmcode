"""H7: Plugin dependency resolver."""
from __future__ import annotations

import pytest

from llm_code.marketplace.dependency_resolver import (
    DependencyError,
    find_reverse_dependents,
    satisfies,
    validate_dependencies,
)


class TestSatisfies:
    def test_equal_version_satisfies(self) -> None:
        assert satisfies("1.2.3", ">=1.0.0") is True

    def test_below_minimum_fails(self) -> None:
        assert satisfies("0.9.0", ">=1.0.0") is False

    def test_handles_missing_spec(self) -> None:
        # Empty spec means "any version".
        assert satisfies("1.2.3", "") is True

    def test_non_numeric_version_fails_gracefully(self) -> None:
        assert satisfies("abc", ">=1.0.0") is False


class TestValidateDependencies:
    def test_all_satisfied(self) -> None:
        manifest = {"id": "pluginA", "dependencies": {"base": ">=1.0.0"}}
        installed = {"base": "2.0.0"}
        validate_dependencies(manifest, installed)  # must not raise

    def test_missing_dependency_raises(self) -> None:
        manifest = {"id": "pluginA", "dependencies": {"base": ">=1.0.0"}}
        with pytest.raises(DependencyError, match="base"):
            validate_dependencies(manifest, {})

    def test_version_too_old_raises(self) -> None:
        manifest = {"id": "pluginA", "dependencies": {"base": ">=2.0.0"}}
        with pytest.raises(DependencyError, match="2.0.0"):
            validate_dependencies(manifest, {"base": "1.0.0"})

    def test_no_deps_ok(self) -> None:
        validate_dependencies({"id": "pluginA"}, {})


class TestFindReverseDependents:
    def test_finds_direct_dependents(self) -> None:
        manifests = [
            {"id": "A", "dependencies": {"B": ">=1.0"}},
            {"id": "C", "dependencies": {"B": ">=1.0", "A": ">=0"}},
            {"id": "B", "dependencies": {}},
        ]
        dependents = find_reverse_dependents("B", manifests)
        assert sorted(dependents) == ["A", "C"]

    def test_returns_empty_when_unused(self) -> None:
        manifests = [
            {"id": "A", "dependencies": {}},
            {"id": "B", "dependencies": {}},
        ]
        assert find_reverse_dependents("X", manifests) == []

    def test_skips_self(self) -> None:
        manifests = [{"id": "A", "dependencies": {"A": ">=1.0"}}]
        # Self-dependency is ignored — not counted as "reverse" by another
        assert find_reverse_dependents("A", manifests) == []

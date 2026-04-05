"""Skill dependency resolver — checks and auto-installs missing skill dependencies."""
from __future__ import annotations

import logging
from packaging.version import Version

from llm_code.runtime.skills import Skill, SkillDependency

_log = logging.getLogger(__name__)


def _get_llm_code_version() -> str:
    """Return the installed llm-code version."""
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("llm-code")
    except Exception:
        return "0.0.0"


class SkillResolver:
    """Check and resolve skill dependencies."""

    def __init__(
        self,
        installed_skills: set[str],
        installer: object,
        max_depth: int = 3,
    ) -> None:
        self._installed = installed_skills
        self._installer = installer
        self._max_depth = max_depth

    def find_missing(self, skill: Skill) -> list[SkillDependency]:
        """Return list of dependencies not currently installed."""
        return [dep for dep in skill.depends if dep.name not in self._installed]

    def _check_cycle(self, name: str, visited: frozenset[str]) -> None:
        """Raise ValueError if name is already in the visited set."""
        if name in visited:
            raise ValueError(f"Circular dependency detected: '{name}' already in chain {sorted(visited)}")

    def _check_depth(self, depth: int) -> None:
        """Raise ValueError if depth exceeds max_depth."""
        if depth > self._max_depth:
            raise ValueError(f"Dependency depth {depth} exceeds max depth {self._max_depth}")

    def check_min_version(self, skill: Skill) -> list[str]:
        """Check if llm-code version satisfies skill's min_version. Return warnings."""
        if not skill.min_version:
            return []
        current = _get_llm_code_version()
        try:
            if Version(current) < Version(skill.min_version):
                return [
                    f"Skill '{skill.name}' requires llm-code >= {skill.min_version}, "
                    f"but current version is {current}"
                ]
        except Exception:
            return [f"Could not compare versions: current={current}, required={skill.min_version}"]
        return []

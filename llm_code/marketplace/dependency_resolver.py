"""Plugin dependency resolver (H7).

Minimal semver-ish satisfies / reverse-deps / validate. Supports the
common cases: ``>=X.Y.Z``, exact ``X.Y.Z``, and empty spec (any).
Complex spec shapes (caret / tilde / ranges) are not needed by the
llm-code marketplace today; add them when a plugin actually requires
one.
"""
from __future__ import annotations

import re


class DependencyError(RuntimeError):
    """Raised when ``validate_dependencies`` detects a missing /
    too-old requirement."""


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _parse_version(v: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(v)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def satisfies(version: str, spec: str) -> bool:
    """Does ``version`` meet ``spec``?"""
    if not spec or not spec.strip():
        return True
    v = _parse_version(version)
    if v is None:
        return False
    spec = spec.strip()
    if spec.startswith(">="):
        floor = _parse_version(spec[2:].strip())
        return bool(floor) and v >= floor
    # Exact match.
    exact = _parse_version(spec)
    return bool(exact) and v == exact


def validate_dependencies(
    manifest: dict,
    installed: dict[str, str],
) -> None:
    """Raise :class:`DependencyError` when ``manifest.dependencies``
    is unmet by ``installed``."""
    deps = manifest.get("dependencies") or {}
    for dep_id, spec in deps.items():
        got = installed.get(dep_id)
        if got is None:
            raise DependencyError(
                f"plugin {manifest.get('id')!r} requires {dep_id!r} "
                f"but it is not installed"
            )
        if not satisfies(got, spec):
            raise DependencyError(
                f"plugin {manifest.get('id')!r} requires {dep_id} {spec} "
                f"but installed version is {got}"
            )


def find_reverse_dependents(
    plugin_id: str, manifests: list[dict],
) -> list[str]:
    """Return ids of plugins whose manifests list ``plugin_id`` as a dep."""
    results: list[str] = []
    for m in manifests:
        mid = m.get("id")
        if mid == plugin_id:
            continue  # skip self-reference
        deps = m.get("dependencies") or {}
        if plugin_id in deps and mid is not None:
            results.append(mid)
    return results

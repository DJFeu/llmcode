"""Rewriter — bump ``llmcode`` dep constraint to ``>=2.0,<3.0``.

Supports three pyproject layouts:

* **Poetry** — ``[tool.poetry.dependencies]`` + ``[tool.poetry.group.*.dependencies]``.
* **PEP 621** — ``[project].dependencies`` / ``[project.optional-dependencies]``.
* **Hatch** — ``[project]`` + ``[tool.hatch.envs.*.dependencies]``.

Uses :mod:`tomlkit` for round-trip fidelity (preserves comment/whitespace
order in the rewritten file). ``tomlkit`` is shipped under the
``[migrate]`` optional-dependency group; if unavailable we fall back to
``tomllib`` for a read-only pass and emit a diagnostic so the user knows
to install the ``migrate`` extra.

The rewritten constraint reads ``>=2.0,<3.0``. Plugin authors can tighten
further by hand (e.g. ``>=2.0,<2.1`` while adopting v12) — the codemod
intentionally provides the widest safe range.
"""
from __future__ import annotations

from typing import Any

try:
    import tomlkit
    from tomlkit.items import Array, InlineTable, String, Table

    _HAS_TOMLKIT = True
except ImportError:  # pragma: no cover — tomlkit ships with the migrate extra
    _HAS_TOMLKIT = False

from llm_code.migrate.v12.diagnostics import Diagnostics

#: Constraint the rewriter writes. Widest safe range for v12 plugins.
V12_CONSTRAINT = ">=2.0,<3.0"

#: Package names this rewriter matches (plus the hyphenated variant).
_TARGET_NAMES = frozenset({"llmcode", "llmcode-cli"})


def rewrite_pyproject_source(
    source: str, path: str, diagnostics: Diagnostics
) -> str:
    """Return the rewritten ``pyproject.toml`` source or the original.

    The function never raises — parse errors are captured as diagnostics
    and the original source is returned unchanged.
    """
    if not _HAS_TOMLKIT:
        diagnostics.report(
            pattern="tomlkit_not_installed",
            path=path,
            line=0,
            rewriter="pyproject_constraint",
            suggestion=(
                "pip install 'llmcode[migrate]' to enable pyproject "
                "rewriting; the codemod needs tomlkit for round-trip "
                "preservation."
            ),
        )
        return source

    try:
        doc = tomlkit.parse(source)
    except Exception as exc:
        diagnostics.report(
            pattern="pyproject_parse_error",
            path=path,
            line=0,
            rewriter="pyproject_constraint",
            suggestion=f"tomlkit could not parse the file: {exc!r}",
        )
        return source

    changed = False

    # --- PEP 621 (project.dependencies / project.optional-dependencies) ---
    project = doc.get("project")
    if isinstance(project, Table):
        deps = project.get("dependencies")
        if isinstance(deps, Array):
            changed = _rewrite_pep621_array(deps) or changed
        optional = project.get("optional-dependencies")
        if isinstance(optional, Table):
            for key in list(optional.keys()):
                group = optional.get(key)
                if isinstance(group, Array):
                    changed = _rewrite_pep621_array(group) or changed

    tool = doc.get("tool")
    if isinstance(tool, Table):
        # --- Poetry ---
        poetry = tool.get("poetry")
        if isinstance(poetry, Table):
            poetry_deps = poetry.get("dependencies")
            if isinstance(poetry_deps, Table):
                changed = _rewrite_poetry_table(poetry_deps) or changed
            groups = poetry.get("group")
            if isinstance(groups, Table):
                for gkey in list(groups.keys()):
                    gval = groups.get(gkey)
                    if isinstance(gval, Table):
                        g_deps = gval.get("dependencies")
                        if isinstance(g_deps, Table):
                            changed = _rewrite_poetry_table(g_deps) or changed

        # --- Hatch envs (uses PEP 508 list, same as PEP 621) ---
        hatch = tool.get("hatch")
        if isinstance(hatch, Table):
            envs = hatch.get("envs")
            if isinstance(envs, Table):
                for ekey in list(envs.keys()):
                    eval_ = envs.get(ekey)
                    if isinstance(eval_, Table):
                        e_deps = eval_.get("dependencies")
                        if isinstance(e_deps, Array):
                            changed = _rewrite_pep621_array(e_deps) or changed

    if not changed:
        return source

    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rewrite_pep621_array(array: "Array") -> bool:
    """Rewrite an in-place PEP 508 list.

    Returns True iff any element was mutated.
    """
    changed = False
    for idx, item in enumerate(array):
        if not isinstance(item, str) and not _is_tomlkit_string(item):
            continue
        raw = str(item)
        new = _rewrite_pep508(raw)
        if new is not None and new != raw:
            array[idx] = new  # type: ignore[index]
            changed = True
    return changed


def _rewrite_poetry_table(table: "Table") -> bool:
    """Rewrite the poetry dep table (``{ name = version | { version = ... } }``)."""
    changed = False
    for key in list(table.keys()):
        if key not in _TARGET_NAMES:
            continue
        value = table.get(key)
        # Simple: name = ">=1.0"
        if isinstance(value, str) or _is_tomlkit_string(value):
            if str(value) != V12_CONSTRAINT:
                table[key] = V12_CONSTRAINT
                changed = True
            continue
        # Extended: name = { version = ">=1.0", extras = [...] }
        if isinstance(value, (Table, InlineTable)) and "version" in value:
            if str(value["version"]) != V12_CONSTRAINT:
                value["version"] = V12_CONSTRAINT
                changed = True
            continue
    return changed


def _rewrite_pep508(spec: str) -> str | None:
    """Return a rewritten PEP 508 spec, or None if unrelated."""
    # Strip env-marker suffix (``name>=1 ; python_version<"3.11"``) to
    # isolate the bare ``name[spec]`` portion.
    marker = ""
    if ";" in spec:
        spec_body, marker_body = spec.split(";", 1)
        marker = f";{marker_body}"
        bare = spec_body.strip()
    else:
        bare = spec.strip()

    # Extract the dependency name (everything before the first of
    # ``[`` (extras), space, or a version operator).
    name_end = len(bare)
    for token in ("[", " ", ">=", "<=", "==", "!=", "~=", ">", "<"):
        idx = bare.find(token)
        if idx != -1 and idx < name_end:
            name_end = idx
    name = bare[:name_end].strip().lower()
    if name not in _TARGET_NAMES:
        return None

    # Preserve any ``[extras]`` cluster attached to the name.
    rest = bare[name_end:]
    extras = ""
    if rest.startswith("["):
        close = rest.find("]")
        if close != -1:
            extras = rest[: close + 1]

    prefix = bare[:name_end] + extras
    return f"{prefix}{V12_CONSTRAINT}{marker}"


def _is_tomlkit_string(value: Any) -> bool:
    """Cheap isinstance guard for the rare bare import layout."""
    return _HAS_TOMLKIT and isinstance(value, String)

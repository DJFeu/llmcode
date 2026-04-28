"""Resolve on-disk paths for the wheel-bundled built-in profiles.

The TOML files ship inside :mod:`llm_code._builtins.profiles`. We use
:mod:`importlib.resources` so the same code path works for editable
installs (paths point straight at ``llm_code/_builtins/profiles/``)
and wheel installs (paths point at the materialised package data
under ``site-packages``).

Public surface (also re-exported from :mod:`llm_code.profiles`):

* :func:`builtin_profile_dir` — directory containing the bundled
  ``*.toml`` files.
* :func:`list_builtin_profile_paths` — sorted list of every bundled
  profile path.
* :func:`builtin_profile_path` — friendly-name lookup. Accepts either
  the bare filename stem (``65-glm-5.1``) or the trailing component
  after the optional numeric prefix (``glm-5.1``).
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = [
    "builtin_profile_dir",
    "builtin_profile_path",
    "list_builtin_profile_paths",
    "strip_numeric_prefix",
]


_PACKAGE = "llm_code._builtins.profiles"


def builtin_profile_dir() -> Path:
    """Return the on-disk path to the bundled built-in profiles dir.

    Resolves via :mod:`importlib.resources` so the same call works for
    editable installs and wheel installs. The returned path always
    points at a real directory containing the ``*.toml`` profiles.
    """
    return Path(str(resources.files(_PACKAGE)))


def list_builtin_profile_paths() -> list[Path]:
    """Sorted list of every bundled ``.toml`` profile path."""
    base = builtin_profile_dir()
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.suffix == ".toml")


def strip_numeric_prefix(stem: str) -> str:
    """Return the trailing slug of a numerically prefixed filename stem.

    The bundled profiles use a ``NN-name`` filename convention so the
    sort order doubles as a presentation order. Users typically know a
    profile by the trailing slug (``glm-5.1``) rather than the stem
    (``65-glm-5.1``), so the CLI accepts both shapes.

    Examples:
        ``"65-glm-5.1"`` → ``"glm-5.1"``
        ``"30-claude-sonnet"`` → ``"claude-sonnet"``
        ``"glm"`` → ``"glm"`` (no numeric prefix → returned unchanged)
    """
    if len(stem) >= 2 and stem[:2].isdigit() and "-" in stem:
        return stem.split("-", 1)[1]
    return stem


def builtin_profile_path(name: str) -> Path | None:
    """Look up a built-in profile by friendly name.

    Matches case-insensitively against either:

    * the bare filename stem (e.g. ``65-glm-5.1``)
    * the trailing component after the numeric prefix (e.g. ``glm-5.1``)

    Returns the resolved :class:`~pathlib.Path` or ``None`` when no
    bundled profile matches.
    """
    if not name:
        return None
    target = name.lower().strip()
    # Allow ``glm-5.1.toml`` for callers that paste a filename.
    if target.endswith(".toml"):
        target = target[: -len(".toml")]
    for path in list_builtin_profile_paths():
        stem = path.stem.lower()
        bare = strip_numeric_prefix(stem)
        if target in (stem, bare):
            return path
    return None

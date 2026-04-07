"""Built-in configuration presets ported from oh-my-opencode docs/configurations.md.

Each preset is a JSON file in this directory. Use :func:`load_preset` to fetch
one by name; :func:`available_presets` lists all installed presets.
"""
from __future__ import annotations

import json
from pathlib import Path

_PRESET_DIR = Path(__file__).parent


def available_presets() -> list[str]:
    """Return the list of installed preset names (without .json extension)."""
    return sorted(p.stem for p in _PRESET_DIR.glob("*.json"))


def load_preset(name: str) -> dict | None:
    """Load preset *name* and return its contents, or None if missing/invalid."""
    path = _PRESET_DIR / f"{name}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


__all__ = ["available_presets", "load_preset"]

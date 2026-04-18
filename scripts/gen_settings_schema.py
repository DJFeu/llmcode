#!/usr/bin/env python
"""Regenerate ``schemas/settings.schema.json`` from :class:`RuntimeConfig`.

Run from the repo root::

    python scripts/gen_settings_schema.py

The schema is committed so VS Code / JetBrains ``$schema`` refs keep
working offline. Re-run whenever ``llm_code/runtime/config.py`` grows
a new dataclass field.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llm_code.config.schema_export import write_schema_file  # noqa: E402
from llm_code.runtime.config import RuntimeConfig  # noqa: E402

OUT = REPO_ROOT / "schemas" / "settings.schema.json"


def main() -> int:
    write_schema_file(RuntimeConfig, OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

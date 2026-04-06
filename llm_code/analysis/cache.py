"""Analysis result cache -- save/load to .llmcode/last_analysis.json."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from llm_code.analysis.rules import Violation


def save_results(cwd: Path, violations: tuple[Violation, ...]) -> Path:
    """Save violations to .llmcode/last_analysis.json."""
    cache_dir = cwd / ".llmcode"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "last_analysis.json"
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "violations": [v.to_dict() for v in violations],
    }
    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cache_path


def load_results(cwd: Path) -> tuple[Violation, ...]:
    """Load cached violations. Returns empty tuple if no cache."""
    cache_path = cwd / ".llmcode" / "last_analysis.json"
    if not cache_path.exists():
        return ()
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return tuple(Violation.from_dict(v) for v in data.get("violations", []))
    except (json.JSONDecodeError, KeyError, TypeError):
        return ()

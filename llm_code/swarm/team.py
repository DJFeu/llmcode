"""Team template — save/load reusable agent team configurations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TeamMemberTemplate:
    """A template for a single team member."""
    role: str
    model: str = ""
    backend: str = ""
    system_prompt: str = ""


@dataclass(frozen=True)
class TeamTemplate:
    """A reusable team configuration."""
    name: str
    description: str
    members: tuple[TeamMemberTemplate, ...]
    coordinator_model: str = ""
    max_timeout: int = 600


def save_team(team: TeamTemplate, teams_dir: Path) -> Path:
    teams_dir.mkdir(parents=True, exist_ok=True)
    path = teams_dir / f"{team.name}.json"
    data = {
        "name": team.name,
        "description": team.description,
        "members": [
            {"role": m.role, "model": m.model, "backend": m.backend, "system_prompt": m.system_prompt}
            for m in team.members
        ],
        "coordinator_model": team.coordinator_model,
        "max_timeout": team.max_timeout,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_team(name: str, teams_dir: Path) -> TeamTemplate:
    path = teams_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Team template not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    members = tuple(
        TeamMemberTemplate(
            role=m["role"], model=m.get("model", ""),
            backend=m.get("backend", ""), system_prompt=m.get("system_prompt", ""),
        )
        for m in data.get("members", [])
    )
    return TeamTemplate(
        name=data["name"], description=data.get("description", ""),
        members=members, coordinator_model=data.get("coordinator_model", ""),
        max_timeout=data.get("max_timeout", 600),
    )


def list_teams(teams_dir: Path) -> list[str]:
    if not teams_dir.is_dir():
        return []
    return sorted(p.stem for p in teams_dir.glob("*.json"))

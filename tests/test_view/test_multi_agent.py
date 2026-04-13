"""Tests for M15 Group E multi-agent components (E1-E3)."""
from __future__ import annotations

from rich.console import Console

from llm_code.view.repl import style
from llm_code.view.repl.components.agent_label import (
    color_for_agent,
    render_agent_label,
)
from llm_code.view.repl.components.coordinator_status import (
    AgentStatus,
    render_coordinator_status,
)
from llm_code.view.repl.components.plan_approval import (
    render_plan_approval,
    render_task_assignment,
)


def _render(r) -> str:
    c = Console(width=100, record=True, color_system="truecolor")
    c.print(r)
    return c.export_text()


# === E1 Agent label ===


def test_agent_label_has_name_prefix() -> None:
    out = _render(render_agent_label("researcher", "Found 3 matches."))
    assert "[researcher]" in out
    assert "Found 3 matches." in out


def test_color_for_agent_is_stable() -> None:
    assert color_for_agent("alpha") == color_for_agent("alpha")


def test_color_for_agent_picks_from_palette() -> None:
    color = color_for_agent("alpha")
    assert color in style.palette.agent_palette


def test_different_agents_may_get_different_colors() -> None:
    names = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    colors = {color_for_agent(n) for n in names}
    # Not guaranteed all 6 distinct for small N, but at least two.
    assert len(colors) >= 2


# === E2 Coordinator status ===


def test_coordinator_status_lists_all_agents() -> None:
    agents = [
        AgentStatus(name="alpha", state="running", task="search files"),
        AgentStatus(name="beta", state="completed", task="analyze results"),
        AgentStatus(name="gamma", state="idle"),
    ]
    out = _render(render_coordinator_status(agents))
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "search files" in out


def test_coordinator_status_title_is_swarm() -> None:
    out = _render(render_coordinator_status([]))
    assert "swarm coordinator" in out


# === E3 Plan + task assignment ===


def test_plan_approval_panel() -> None:
    out = _render(render_plan_approval("1. Read file\n2. Edit file\n3. Save"))
    assert "plan awaiting approval" in out
    assert "Read file" in out


def test_task_assignment_renders_id_and_title() -> None:
    out = _render(render_task_assignment("T-42", "Fix flaky test"))
    assert "T-42" in out
    assert "Fix flaky test" in out

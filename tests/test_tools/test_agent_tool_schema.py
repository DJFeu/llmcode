"""AgentTool schema enum coverage."""
from __future__ import annotations

from llm_code.tools.agent import AgentTool


def _make() -> AgentTool:
    def _factory(*_a, **_k):
        raise NotImplementedError
    return AgentTool(runtime_factory=_factory, max_depth=3, current_depth=0)


def test_role_enum_includes_all_five_built_ins() -> None:
    schema = _make().input_schema
    enum = schema["properties"]["role"]["enum"]
    assert set(enum) == {"build", "plan", "explore", "verify", "general"}


def test_unknown_role_returns_error() -> None:
    tool = _make()
    result = tool.execute({"task": "x", "role": "totally_made_up"})
    assert result.is_error is True
    assert "unknown role" in result.output.lower()

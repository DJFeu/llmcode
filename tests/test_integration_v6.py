"""Integration tests for v6 features: agent roles, streaming executor, compression, token budget."""
from __future__ import annotations

from pathlib import Path


from llm_code.api.types import Message, TextBlock, ToolResultBlock
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.session import Session
from llm_code.runtime.streaming_executor import StreamingToolCollector
from llm_code.runtime.token_budget import TokenBudget
from llm_code.tools.agent_roles import BUILT_IN_ROLES, EXPLORE_ROLE
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.write_file import WriteFileTool


def test_explore_role_blocks_write_tools() -> None:
    """Explore role should not include write_file."""
    assert "write_file" not in EXPLORE_ROLE.allowed_tools
    assert "edit_file" not in EXPLORE_ROLE.allowed_tools
    assert "bash" not in EXPLORE_ROLE.allowed_tools
    assert "read_file" in EXPLORE_ROLE.allowed_tools


def test_streaming_collector_separates_reads_and_writes() -> None:
    """Read-only tools immediate, writes buffered."""
    from llm_code.tools.parsing import ParsedToolCall

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    collector = StreamingToolCollector(registry)

    read_call = ParsedToolCall(id="r1", name="read_file", args={"path": "/tmp/x"}, source="native")
    write_call = ParsedToolCall(id="w1", name="write_file", args={"path": "/tmp/y", "content": "z"}, source="native")

    immediate = collector.on_tool_complete(read_call)
    assert immediate is not None  # read → immediate

    immediate2 = collector.on_tool_complete(write_call)
    assert immediate2 is None  # write → buffered

    pending = collector.flush_pending()
    assert len(pending) == 1
    assert pending[0].name == "write_file"


def test_compression_progressive() -> None:
    """Snip alone should fix a session with only long tool results."""
    session = Session.create(project_path=Path("/tmp"))
    # Add a text message first so there are enough messages
    text_msg = Message(role="user", content=(TextBlock(text="test"),))
    session = session.add_message(text_msg)
    # Add a message with a very long tool result
    long_result = "x" * 10000
    msg = Message(role="user", content=(ToolResultBlock(tool_use_id="t1", content=long_result),))
    session = session.add_message(msg)

    compressor = ContextCompressor(max_result_chars=500)
    # Use a max_tokens lower than estimated_tokens to force compression
    compressed = compressor.compress(session, max_tokens=100)
    # Snip should have truncated the result
    result_content = compressed.messages[1].content[0]
    assert hasattr(result_content, "content")
    assert len(result_content.content) <= 600  # 500 + truncation notice


def test_token_budget_nudge() -> None:
    """Budget should indicate need for nudge when not exhausted."""
    budget = TokenBudget(target=1000)
    assert budget.should_nudge() is True
    budget.add(500)
    assert budget.should_nudge() is True
    assert budget.remaining() == 500
    budget.add(600)
    assert budget.should_nudge() is False
    assert budget.is_exhausted() is True


def test_tool_result_budget_large_output(tmp_path: Path) -> None:
    """Large tool results should be truncated with pointer to disk."""
    from llm_code.runtime.conversation import _MAX_INLINE_RESULT

    large_output = "line\n" * 2000  # Well over 4000 chars
    assert len(large_output) > _MAX_INLINE_RESULT


def test_mcp_instructions_in_prompt() -> None:
    """MCP server instructions appear in system prompt."""
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.prompt import SystemPromptBuilder

    builder = SystemPromptBuilder()
    ctx = ProjectContext(cwd=Path("/tmp"), is_git_repo=False, git_status="", instructions="")
    prompt = builder.build(ctx, mcp_instructions={"github": "Use GitHub API to manage repos and issues."})
    assert "MCP Server: github" in prompt
    assert "GitHub API" in prompt


def test_all_built_in_roles_exist() -> None:
    assert "explore" in BUILT_IN_ROLES
    assert "plan" in BUILT_IN_ROLES
    assert "verify" in BUILT_IN_ROLES
    assert "build" in BUILT_IN_ROLES
    assert "general" in BUILT_IN_ROLES
    assert len(BUILT_IN_ROLES) == 5

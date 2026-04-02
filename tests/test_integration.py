"""Integration tests: mock provider + real tools on tmp dirs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import StreamEvent, StreamMessageStop, StreamTextDelta, TokenUsage
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.hooks import HookRunner
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.bash import BashTool
from llm_code.tools.edit_file import EditFileTool
from llm_code.tools.glob_search import GlobSearchTool
from llm_code.tools.grep_search import GrepSearchTool
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.write_file import WriteFileTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_registry() -> ToolRegistry:
    """Create a ToolRegistry with all real tools registered."""
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(GlobSearchTool())
    registry.register(GrepSearchTool())
    return registry


def _build_runtime(
    provider: object,
    tmp_path: Path,
) -> ConversationRuntime:
    """Assemble a ConversationRuntime with real tools and AUTO_ACCEPT permissions."""
    registry = _build_registry()
    policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)
    hook_runner = HookRunner(hooks=())
    prompt_builder = SystemPromptBuilder()
    config = RuntimeConfig(max_turn_iterations=10, max_tokens=4096, temperature=0.0)
    session = Session.create(project_path=tmp_path)
    context = ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )
    return ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=policy,
        hook_runner=hook_runner,
        prompt_builder=prompt_builder,
        config=config,
        session=session,
        context=context,
    )


def _xml_tool_call(tool_name: str, args: dict) -> str:
    """Build an XML-wrapped tool call string as the mock provider would emit."""
    payload = json.dumps({"tool": tool_name, "args": args})
    return f"<tool_call>{payload}</tool_call>"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_file_via_agent(tmp_path: Path) -> None:
    """Agent uses write_file tool to create a file; verify the file appears on disk."""
    target = tmp_path / "hello.txt"
    file_content = "Hello from agent!"

    async def _gen_call1() -> AsyncIterator[StreamEvent]:
        xml = _xml_tool_call("write_file", {"path": str(target), "content": file_content})
        yield StreamTextDelta(text=xml)
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    async def _gen_call2() -> AsyncIterator[StreamEvent]:
        yield StreamTextDelta(text="Done!")
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=20, output_tokens=5),
            stop_reason="end_turn",
        )

    streams = [_gen_call1(), _gen_call2()]
    stream_iter = iter(streams)

    class _Provider:
        async def stream_message(self, request):  # noqa: ANN001
            return next(stream_iter)

        def supports_native_tools(self) -> bool:
            return False

        def supports_images(self) -> bool:
            return False

    provider = _Provider()
    runtime = _build_runtime(provider, tmp_path)

    # Drain the async generator
    events = []
    async for event in runtime.run_turn("Please write a hello file"):
        events.append(event)

    # File must exist on disk with correct content
    assert target.exists(), f"Expected file {target} to be created by agent"
    assert target.read_text() == file_content

    # Session must have at least 3 messages:
    # [user_input, assistant_tool_call, tool_result, assistant_final]
    assert len(runtime.session.messages) >= 3


@pytest.mark.asyncio
async def test_read_then_edit(tmp_path: Path) -> None:
    """Agent reads a file then edits it; verify old text is replaced with new."""
    src_file = tmp_path / "config.py"
    src_file.write_text("name = 'old'\n")

    async def _gen_call1() -> AsyncIterator[StreamEvent]:
        xml = _xml_tool_call("read_file", {"path": str(src_file)})
        yield StreamTextDelta(text=xml)
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    async def _gen_call2() -> AsyncIterator[StreamEvent]:
        xml = _xml_tool_call(
            "edit_file",
            {"path": str(src_file), "old": "old", "new": "new"},
        )
        yield StreamTextDelta(text=xml)
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=15, output_tokens=8),
            stop_reason="end_turn",
        )

    async def _gen_call3() -> AsyncIterator[StreamEvent]:
        yield StreamTextDelta(text="Updated")
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=20, output_tokens=5),
            stop_reason="end_turn",
        )

    streams = [_gen_call1(), _gen_call2(), _gen_call3()]
    stream_iter = iter(streams)

    class _Provider:
        async def stream_message(self, request):  # noqa: ANN001
            return next(stream_iter)

        def supports_native_tools(self) -> bool:
            return False

        def supports_images(self) -> bool:
            return False

    provider = _Provider()
    runtime = _build_runtime(provider, tmp_path)

    events = []
    async for event in runtime.run_turn("Read config.py and rename 'old' to 'new'"):
        events.append(event)

    final_content = src_file.read_text()
    assert "new" in final_content, f"Expected 'new' in file, got: {final_content!r}"
    assert "old" not in final_content, f"Expected 'old' to be gone, got: {final_content!r}"

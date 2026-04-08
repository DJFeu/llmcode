"""Shared ConversationRuntime factory for tests.

Before this module existed, multiple test files each hand-built a
ConversationRuntime with ad-hoc fake-provider classes. That duplication
is why PR #17's smoke tombstone couldn't be filled — every test paid
the cost of the 17-argument constructor.

``make_conv_runtime(...)`` returns a ready-to-use runtime with sensible
defaults. Override what you need:

    runtime = make_conv_runtime(
        canned_response_text='<tool_call>bash>{"args":{"command":"ls"}}</tool_call>',
        extra_tools={"bash": lambda args: {"output": "file1\\nfile2", "is_error": False}},
    )
    await runtime.run_one_turn("list files")
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from llm_code.api.types import (
    MessageRequest,
    MessageResponse,
    StreamMessageStop,
    StreamTextDelta,
    TextBlock,
    TokenUsage,
)
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry


class _CannedStreamProvider:
    """Provider that yields a fixed text as a single StreamTextDelta
    followed by a StreamMessageStop. Sufficient for tool-call parser
    tests; no real HTTP calls."""

    def __init__(self, text: str) -> None:
        self._text = text

    def supports_native_tools(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False

    async def stream_message(self, request: MessageRequest) -> Any:
        text = self._text

        async def _gen():
            yield StreamTextDelta(text=text)
            yield StreamMessageStop(
                usage=TokenUsage(
                    input_tokens=10,
                    output_tokens=max(1, len(text) // 4),
                ),
                stop_reason="stop",
            )

        return _gen()

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        return MessageResponse(
            content=(TextBlock(text=self._text),),
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=max(1, len(self._text) // 4),
            ),
            stop_reason="stop",
        )


class _CallbackTool(Tool):
    """Tool that delegates execution to an arbitrary callable. Lets
    tests assert "was this tool dispatched with these args?"."""

    def __init__(self, name: str, callback: Callable[[dict], dict]) -> None:
        self._name = name
        self._callback = callback

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}, "additionalProperties": True}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        result = self._callback(args)
        return ToolResult(
            output=result.get("output", ""),
            is_error=result.get("is_error", False),
        )


def make_conv_runtime(
    *,
    canned_response_text: str = "",
    extra_tools: dict[str, Callable[[dict], dict]] | None = None,
    config: RuntimeConfig | None = None,
    cwd: Path | None = None,
) -> ConversationRuntime:
    """Build a ConversationRuntime wired with a canned-response provider
    and a minimal tool registry containing only the tools you name.
    """
    provider = _CannedStreamProvider(canned_response_text)
    registry = ToolRegistry()
    if extra_tools:
        for tool_name, cb in extra_tools.items():
            registry.register(_CallbackTool(tool_name, cb))
    cfg = config or RuntimeConfig()
    project_path = cwd or Path.cwd()
    session = Session.create(project_path=project_path)
    context = ProjectContext(
        cwd=project_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )
    prompt_builder = SystemPromptBuilder()
    permissions = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)
    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=permissions,
        hook_runner=None,
        prompt_builder=prompt_builder,
        config=cfg,
        session=session,
        context=context,
    )
    return runtime

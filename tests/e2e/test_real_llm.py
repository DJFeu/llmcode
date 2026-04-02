"""End-to-end tests against a real LLM.

Run manually with:
    LLM_API_BASE=http://localhost:8000/v1 LLM_MODEL=qwen3.5 pytest tests/e2e/ -v

These tests are skipped by default unless LLM_API_BASE is set.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("LLM_API_BASE"),
    reason="LLM_API_BASE not set — skip real LLM tests"
)


@pytest.fixture
def api_base():
    return os.environ["LLM_API_BASE"]


@pytest.fixture
def model():
    return os.environ.get("LLM_MODEL", "default")


@pytest.mark.asyncio
async def test_simple_chat(api_base, model):
    """Basic: send a message, get a text response."""
    from llm_code.api.client import ProviderClient
    from llm_code.api.types import Message, MessageRequest, TextBlock

    provider = ProviderClient.from_model(model=model, base_url=api_base)
    request = MessageRequest(
        model=model,
        messages=(Message(role="user", content=(TextBlock(text="Say hello in one word."),)),),
        max_tokens=50,
        stream=False,
    )
    response = await provider.send_message(request)
    assert len(response.content) > 0
    text = response.content[0]
    assert hasattr(text, "text")
    assert len(text.text) > 0
    print(f"Response: {text.text}")


@pytest.mark.asyncio
async def test_streaming(api_base, model):
    """Streaming: verify events arrive incrementally."""
    from llm_code.api.client import ProviderClient
    from llm_code.api.types import Message, MessageRequest, StreamTextDelta, TextBlock

    provider = ProviderClient.from_model(model=model, base_url=api_base)
    request = MessageRequest(
        model=model,
        messages=(Message(role="user", content=(TextBlock(text="Count from 1 to 5."),)),),
        max_tokens=100,
    )
    events = []
    async for event in provider.stream_message(request):
        events.append(event)

    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    assert len(text_events) > 0
    full_text = "".join(e.text for e in text_events)
    print(f"Streamed: {full_text[:200]}")


@pytest.mark.asyncio
async def test_tool_calling(api_base, model, tmp_path):
    """Full agent turn: LLM calls a tool, gets result, responds."""
    from llm_code.api.client import ProviderClient
    from llm_code.runtime.config import RuntimeConfig
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.runtime.hooks import HookRunner
    from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.bash import BashTool
    from llm_code.tools.read_file import ReadFileTool
    from llm_code.tools.write_file import WriteFileTool
    from llm_code.tools.registry import ToolRegistry
    from llm_code.api.types import StreamTextDelta

    # Create a file for the agent to read
    test_file = tmp_path / "hello.py"
    test_file.write_text("print('hello world')")

    provider = ProviderClient.from_model(model=model, base_url=api_base)
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(BashTool())

    runtime = ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=HookRunner(),
        prompt_builder=SystemPromptBuilder(),
        config=RuntimeConfig(model=model, max_turn_iterations=3, native_tools=False),
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(cwd=tmp_path, is_git_repo=False, git_status="", instructions=""),
    )

    text_parts = []
    async for event in runtime.run_turn(f"Read the file at {test_file} and tell me what it does."):
        if isinstance(event, StreamTextDelta):
            text_parts.append(event.text)

    full_response = "".join(text_parts)
    print(f"Agent response: {full_response[:500]}")
    # The agent should have read the file and mentioned hello or print
    assert len(runtime.session.messages) >= 2  # At least user + assistant

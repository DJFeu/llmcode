"""Integration tests for v4 features: checkpoints, git tools, and CLI integration."""
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.api.types import StreamMessageStop, StreamTextDelta, TokenUsage
from llm_code.runtime.checkpoint import CheckpointManager
from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.hooks import HookRunner
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.bash import BashTool
from llm_code.tools.edit_file import EditFileTool
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.write_file import WriteFileTool


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    (tmp_path / "init.txt").write_text("init")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


class _AsyncGenWrapper:
    """Wraps a list of events as an async iterable."""

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for event in self._events:
            yield event


def _make_runtime(responses, tmp_path, with_checkpoint=False):
    call_count = 0

    async def mock_stream(request):
        nonlocal call_count
        call_count += 1
        idx = min(call_count - 1, len(responses) - 1)
        return _AsyncGenWrapper(responses[idx])

    # ProviderClient has a mixed API: stream_message() / close() are
    # async but supports_native_tools() / supports_images() /
    # supports_reasoning() are sync. Using a bare AsyncMock wraps every
    # method as async, so the sync calls produce never-awaited
    # coroutines when runtime invokes them. Explicitly re-declaring the
    # sync methods as MagicMock gives them the right shape and silences
    # the RuntimeWarning.
    provider = AsyncMock()
    provider.stream_message = mock_stream
    provider.supports_native_tools = MagicMock(return_value=False)
    provider.supports_images = MagicMock(return_value=False)
    provider.supports_reasoning = MagicMock(return_value=False)

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())

    checkpoint_mgr = CheckpointManager(tmp_path) if with_checkpoint else None

    return ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=HookRunner(),
        prompt_builder=SystemPromptBuilder(),
        config=RuntimeConfig(max_turn_iterations=5),
        session=Session.create(project_path=tmp_path),
        context=ProjectContext(cwd=tmp_path, is_git_repo=True, git_status="", instructions=""),
        checkpoint_manager=checkpoint_mgr,
    )


@pytest.mark.asyncio
async def test_checkpoint_created_before_write(git_repo):
    target = git_repo / "output.py"
    responses = [
        [
            StreamTextDelta(
                text=f'<tool_call>\n{{"tool": "write_file", "args": {{"path": "{target}", "content": "x=1"}}}}\n</tool_call>'
            ),
            StreamMessageStop(
                usage=TokenUsage(input_tokens=10, output_tokens=5), stop_reason="end_turn"
            ),
        ],
        [
            StreamTextDelta(text="Done."),
            StreamMessageStop(
                usage=TokenUsage(input_tokens=20, output_tokens=5), stop_reason="end_turn"
            ),
        ],
    ]
    runtime = _make_runtime(responses, git_repo, with_checkpoint=True)
    async for _ in runtime.run_turn("write file"):
        pass
    assert target.exists()
    # Checkpoint should have been created
    assert runtime._checkpoint_mgr.can_undo()


@pytest.mark.asyncio
async def test_undo_restores_state(git_repo):
    target = git_repo / "to_delete.txt"
    target.write_text("important")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add file"], cwd=git_repo, capture_output=True)

    mgr = CheckpointManager(git_repo)
    mgr.create("edit_file", {"path": str(target)})
    target.write_text("modified")

    cp = mgr.undo()
    assert cp is not None
    assert target.read_text() == "important"


@pytest.mark.asyncio
async def test_read_only_tool_no_checkpoint(git_repo):
    responses = [
        [
            StreamTextDelta(
                text=f'<tool_call>\n{{"tool": "read_file", "args": {{"path": "{git_repo / "init.txt"}"}}}}\n</tool_call>'
            ),
            StreamMessageStop(
                usage=TokenUsage(input_tokens=10, output_tokens=5), stop_reason="end_turn"
            ),
        ],
        [
            StreamTextDelta(text="File content."),
            StreamMessageStop(
                usage=TokenUsage(input_tokens=20, output_tokens=5), stop_reason="end_turn"
            ),
        ],
    ]
    runtime = _make_runtime(responses, git_repo, with_checkpoint=True)
    async for _ in runtime.run_turn("read file"):
        pass
    # read_file is read-only, no checkpoint should be created
    assert not runtime._checkpoint_mgr.can_undo()

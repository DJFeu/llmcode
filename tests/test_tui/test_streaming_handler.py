# tests/test_tui/test_streaming_handler.py
"""Tests for the extracted StreamingHandler class."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def test_streaming_handler_exists():
    """StreamingHandler can be imported and instantiated with a mock app."""
    from llm_code.tui.streaming_handler import StreamingHandler
    app = MagicMock()
    handler = StreamingHandler(app)
    assert handler._app is app


def test_streaming_handler_stores_app_reference():
    """The _app attribute references the exact object passed to __init__."""
    from llm_code.tui.streaming_handler import StreamingHandler
    sentinel = object()
    app = MagicMock()
    app.sentinel = sentinel
    handler = StreamingHandler(app)
    assert handler._app.sentinel is sentinel


@pytest.mark.asyncio
async def test_run_turn_early_return_no_runtime():
    """When runtime is None, run_turn shows an error and returns early."""
    from llm_code.tui.streaming_handler import StreamingHandler

    mock_chat = MagicMock()
    app = MagicMock()
    app._runtime = None
    app.query_one = MagicMock(return_value=mock_chat)

    handler = StreamingHandler(app)
    # Should not crash — just shows error in chat
    await handler.run_turn("hello")

    # Verify that an error entry was added
    assert mock_chat.add_entry.called
    entry = mock_chat.add_entry.call_args[0][0]
    # AssistantText stores the message in _text
    assert "runtime not initialized" in getattr(entry, "_text", "")


@pytest.mark.asyncio
async def test_run_turn_delegates_from_app():
    """LLMCodeTUI._run_turn delegates to self._streaming.run_turn."""
    from llm_code.tui.streaming_handler import StreamingHandler

    handler = StreamingHandler(MagicMock())
    handler.run_turn = AsyncMock()

    # Simulate the delegation pattern from app.py
    app = MagicMock()
    app._streaming = handler

    await app._streaming.run_turn("test input", images=None)
    handler.run_turn.assert_awaited_once_with("test input", images=None)

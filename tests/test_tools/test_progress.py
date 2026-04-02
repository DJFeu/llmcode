"""Tests for ToolProgress and StreamToolProgress types."""
from __future__ import annotations

import dataclasses

import pytest

from llm_code.api.types import StreamEvent, StreamToolProgress
from llm_code.tools.base import ToolProgress


class TestToolProgress:
    def test_creation_with_message_only(self) -> None:
        progress = ToolProgress(tool_name="read_file", message="Reading file...")
        assert progress.tool_name == "read_file"
        assert progress.message == "Reading file..."
        assert progress.percent is None

    def test_creation_with_percent(self) -> None:
        progress = ToolProgress(tool_name="search", message="50% done", percent=50.0)
        assert progress.tool_name == "search"
        assert progress.message == "50% done"
        assert progress.percent == 50.0

    def test_percent_zero_is_valid(self) -> None:
        progress = ToolProgress(tool_name="read_file", message="Starting", percent=0.0)
        assert progress.percent == 0.0

    def test_percent_100_is_valid(self) -> None:
        progress = ToolProgress(tool_name="read_file", message="Done", percent=100.0)
        assert progress.percent == 100.0

    def test_frozen(self) -> None:
        progress = ToolProgress(tool_name="read_file", message="msg")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            progress.message = "changed"  # type: ignore[misc]

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ToolProgress)


class TestStreamToolProgress:
    def test_is_stream_event_subclass(self) -> None:
        assert issubclass(StreamToolProgress, StreamEvent)

    def test_creation_with_message_only(self) -> None:
        event = StreamToolProgress(tool_name="read_file", message="Processing...")
        assert event.tool_name == "read_file"
        assert event.message == "Processing..."
        assert event.percent is None

    def test_creation_with_percent(self) -> None:
        event = StreamToolProgress(
            tool_name="search", message="75% done", percent=75.0
        )
        assert event.tool_name == "search"
        assert event.message == "75% done"
        assert event.percent == 75.0

    def test_frozen(self) -> None:
        event = StreamToolProgress(tool_name="read_file", message="msg")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            event.message = "changed"  # type: ignore[misc]

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(StreamToolProgress)

    def test_isinstance_of_stream_event(self) -> None:
        event = StreamToolProgress(tool_name="tool", message="hello")
        assert isinstance(event, StreamEvent)

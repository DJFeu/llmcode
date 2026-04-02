"""Abstract base class for LLM provider implementations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from llm_code.api.types import MessageRequest, MessageResponse, StreamEvent


class LLMProvider(ABC):
    """Interface that all LLM provider adapters must implement.

    Concrete implementations (e.g. OpenAI-compatible, Anthropic) subclass
    this and fill in the four abstract methods.
    """

    @abstractmethod
    async def send_message(self, request: MessageRequest) -> MessageResponse:
        """Send a complete (non-streaming) message and return the full response."""
        ...

    @abstractmethod
    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        """Stream a message and return an async iterator of stream events."""
        ...

    @abstractmethod
    def supports_native_tools(self) -> bool:
        """Return True if the provider supports native/function-calling tools."""
        ...

    @abstractmethod
    def supports_images(self) -> bool:
        """Return True if the provider supports image inputs."""
        ...

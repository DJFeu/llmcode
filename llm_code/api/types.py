"""Frozen dataclass types for the LLM provider API layer."""
from __future__ import annotations

import dataclasses
from typing import Union


@dataclasses.dataclass(frozen=True)
class ThinkingBlock:
    """Structured reasoning/chain-of-thought content from the model.

    Unlike ``TextBlock``, thinking is produced by the model's internal
    reasoning pass before it commits to a user-visible response. Some
    providers (notably Anthropic extended thinking) require these
    blocks to be echoed back verbatim in subsequent requests and will
    reject the request if any bytes of ``signature`` are altered.

    Within an assistant ``Message.content`` tuple, all ThinkingBlocks
    must appear before the first non-thinking block. See
    ``llm_code.api.content_order.validate_assistant_content_order``.

    Providers that do not sign thinking (Qwen, DeepSeek, OpenAI
    o-series) leave ``signature`` as an empty string.
    """

    content: str
    signature: str = ""


@dataclasses.dataclass(frozen=True)
class TextBlock:
    text: str


@dataclasses.dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclasses.dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclasses.dataclass(frozen=True)
class ImageBlock:
    media_type: str
    data: str


@dataclasses.dataclass(frozen=True)
class ServerToolUseBlock:
    """Anthropic server-side tool use (e.g. web search).

    Must be round-tripped with its signature on subsequent turns.
    """
    id: str
    name: str
    input: dict
    signature: str = ""


@dataclasses.dataclass(frozen=True)
class ServerToolResultBlock:
    """Anthropic server-side tool result.

    Must be round-tripped with its signature on subsequent turns.
    """
    tool_use_id: str
    content: str
    signature: str = ""


ContentBlock = Union[
    ThinkingBlock, TextBlock, ToolUseBlock, ToolResultBlock,
    ImageBlock, ServerToolUseBlock, ServerToolResultBlock,
]


@dataclasses.dataclass(frozen=True)
class Message:
    role: str
    content: tuple[ContentBlock, ...]


@dataclasses.dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict


@dataclasses.dataclass(frozen=True)
class MessageRequest:
    model: str
    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolDefinition, ...] = ()
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = True
    extra_body: dict | None = None
    cache_key: str = ""
    metadata: dict | None = None


@dataclasses.dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    # Wave2-2: cache tokens are reported separately by both Anthropic
    # (``cache_read_input_tokens`` / ``cache_creation_input_tokens``) and
    # OpenAI-compat servers (``prompt_tokens_details.cached_tokens``).
    # Default to 0 so existing call sites keep working unchanged.
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclasses.dataclass(frozen=True)
class MessageResponse:
    content: tuple[ContentBlock, ...]
    usage: TokenUsage
    stop_reason: str
    # Wave2-1a P2: thinking blocks parsed from the provider response.
    # Non-thinking providers leave this empty. P3 is where these move
    # into the assembled assistant Message.content; P2 only surfaces
    # them on the response object so downstream assembly can see them.
    thinking: tuple[ThinkingBlock, ...] = ()


@dataclasses.dataclass(frozen=True)
class StreamEvent:
    """Base class for all stream events."""


@dataclasses.dataclass(frozen=True)
class StreamMessageStart(StreamEvent):
    model: str


@dataclasses.dataclass(frozen=True)
class StreamTextDelta(StreamEvent):
    text: str


@dataclasses.dataclass(frozen=True)
class StreamToolUseStart(StreamEvent):
    id: str
    name: str


@dataclasses.dataclass(frozen=True)
class StreamToolUseInputDelta(StreamEvent):
    id: str
    partial_json: str


@dataclasses.dataclass(frozen=True)
class StreamMessageStop(StreamEvent):
    usage: TokenUsage
    stop_reason: str


@dataclasses.dataclass(frozen=True)
class StreamToolProgress(StreamEvent):
    tool_name: str
    message: str
    percent: float | None = None


@dataclasses.dataclass(frozen=True)
class StreamToolExecStart(StreamEvent):
    """Emitted when a tool starts executing."""
    tool_name: str
    args_summary: str
    tool_id: str = ""  # correlation key for matching with StreamToolExecResult
    # H10 deep wire: capability labels computed by the pipeline — lets
    # TUIs / telemetry / audit logs branch on "this call is destructive"
    # without re-inspecting the tool object. Sorted tuple of labels
    # drawn from {read_only, destructive, rollbackable, network}.
    tool_capabilities: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class StreamToolExecResult(StreamEvent):
    """Emitted when a tool finishes executing."""
    tool_name: str
    output: str
    is_error: bool = False
    metadata: dict | None = None
    tool_id: str = ""  # correlation key matching the StreamToolExecStart that opened it


@dataclasses.dataclass(frozen=True)
class StreamThinkingDelta(StreamEvent):
    """Emitted when the model produces a thinking/reasoning token."""
    text: str


@dataclasses.dataclass(frozen=True)
class StreamServerToolBlock(StreamEvent):
    """Emitted when a server-side tool block completes (e.g. web_search).

    Carries the full block data (server_tool_use or server_tool_result)
    so the runtime can store it on the assistant message for round-trip.
    """
    block: "ServerToolUseBlock | ServerToolResultBlock"


@dataclasses.dataclass(frozen=True)
class StreamThinkingSignature(StreamEvent):
    """Emitted when a thinking block's signature is fully accumulated.

    Anthropic streams the cryptographic signature for signed thinking
    blocks as multiple ``signature_delta`` events. This event carries
    the complete, concatenated signature string once the thinking
    content block closes (``content_block_stop``).
    """
    signature: str


@dataclasses.dataclass(frozen=True)
class StreamCompactionStart(StreamEvent):
    """Emitted when auto-compaction starts in the background."""
    used_tokens: int
    max_tokens: int


@dataclasses.dataclass(frozen=True)
class StreamCompactionDone(StreamEvent):
    """Emitted when auto-compaction finishes."""
    before_messages: int
    after_messages: int


@dataclasses.dataclass(frozen=True)
class StreamMCPApprovalRequest(StreamEvent):
    """Emitted when a non-root agent wants to spawn an MCP server."""
    server_name: str
    owner_agent_id: str
    command: str
    description: str


@dataclasses.dataclass(frozen=True)
class StreamPermissionRequest(StreamEvent):
    """Emitted when a tool requires user permission before execution."""
    tool_name: str
    args_preview: str
    diff_lines: tuple[str, ...] = ()
    pending_files: tuple[str, ...] = ()

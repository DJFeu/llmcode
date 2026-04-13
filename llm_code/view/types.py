"""Core view-layer data types shared across all backends.

These types are the 'wire format' between dispatcher/runtime and any
ViewBackend implementation. Keeping them immutable + explicit ensures
backends can't accidentally mutate shared state (which would leak
between backends in a hypothetical multi-backend gateway setup).

Based on hermes-agent's gateway/platforms/base.py MessageEvent /
SendResult pattern, simplified for view-only concerns (no platform-
specific metadata fields like thread_id, voice_message_id, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class Role(Enum):
    """The speaker of a message."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class RiskLevel(Enum):
    """Risk classification used by show_confirm() dialogs.

    Backends use this to color or visually distinguish confirmation
    prompts for destructive actions. A NORMAL-risk confirm might
    render as a quiet dim prompt; a CRITICAL-risk confirm must be
    loud and hard to dismiss accidentally.
    """
    NORMAL = "normal"       # read_file, ls, git status — informational
    ELEVATED = "elevated"   # edit_file, bash (read-only), write in cwd
    HIGH = "high"           # bash (mutating), write outside cwd, network
    CRITICAL = "critical"   # delete files, git push --force, rm -rf


@dataclass(frozen=True)
class MessageEvent:
    """A complete message that gets rendered to the view.

    Used for non-streaming messages: user input echo, system notes,
    compaction markers, tool result summaries for completed turns.

    Streaming assistant responses use StreamingMessageHandle instead
    (see below) because they need incremental updates.
    """
    role: Role
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusUpdate:
    """Partial update to the backend's status display.

    Only non-None fields are applied; existing state persists for
    fields left as None. This lets the dispatcher update one field
    (e.g. just `cost_usd`) without having to re-state the entire
    status vector.

    Mutable on purpose — the dispatcher builds these incrementally
    during a turn and passes one to backend.update_status() at
    turn end (and optionally mid-turn for streaming token counts).
    """
    model: Optional[str] = None
    cwd: Optional[str] = None
    branch: Optional[str] = None
    permission_mode: Optional[str] = None
    context_used_tokens: Optional[int] = None
    context_limit_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    is_streaming: bool = False
    streaming_token_count: Optional[int] = None
    rate_limit_until: Optional[datetime] = None
    rate_limit_reqs_left: Optional[int] = None
    voice_active: bool = False
    voice_seconds: Optional[float] = None
    voice_peak: Optional[float] = None


@runtime_checkable
class StreamingMessageHandle(Protocol):
    """Handle to an in-progress streaming message region.

    Returned by ViewBackend.start_streaming_message(). The caller feeds
    chunks until the response is complete, then calls commit() to
    finalize the region. abort() discards the in-progress content
    (used on Ctrl+C cancellation or error).

    Runtime-checkable so tests can assert ``isinstance(handle, StreamingMessageHandle)``.
    """

    def feed(self, chunk: str) -> None:
        """Append a text chunk to the in-progress message."""
        ...

    def commit(self) -> None:
        """Finalize the message. After commit, feed() becomes a no-op."""
        ...

    def abort(self) -> None:
        """Discard the in-progress message without finalizing."""
        ...

    @property
    def is_active(self) -> bool:
        """True between start_streaming_message() and the first
        commit() / abort() call. False afterward."""
        ...


@runtime_checkable
class ToolEventHandle(Protocol):
    """Handle to an in-progress tool call display.

    REPL backend implements Style R (inline summary by default, diff
    tools and failures auto-expand). Other backends may implement
    different visual treatments but must honor the same feed/commit
    lifecycle.
    """

    def feed_stdout(self, line: str) -> None:
        """Append a stdout line from the running tool."""
        ...

    def feed_stderr(self, line: str) -> None:
        """Append a stderr line from the running tool."""
        ...

    def feed_diff(self, diff_text: str) -> None:
        """Attach a unified diff (for edit_file / write_file / apply_patch).

        Backends that auto-expand diffs (REPL style R) render this
        when commit_success() is called. Backends that don't just
        store it in metadata.
        """
        ...

    def commit_success(
        self,
        *,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Finalize the tool call as successful."""
        ...

    def commit_failure(
        self,
        *,
        error: str,
        exit_code: Optional[int] = None,
    ) -> None:
        """Finalize the tool call as failed. Backends may visually
        distinguish failure (red border, expanded stderr, etc.)."""
        ...

    @property
    def is_active(self) -> bool:
        """True until commit_success or commit_failure is called."""
        ...

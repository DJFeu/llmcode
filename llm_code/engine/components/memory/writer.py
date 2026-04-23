"""MemoryWriterComponent — post-tool persistence stage (v12 M7 Task 7.5).

Writes a :class:`MemoryEntry` to the configured
:class:`MemoryLayer` after a tool call completes. Gated by a
``should_remember`` predicate so we don't store garbage from failed
calls (unless the policy is explicitly ``on_error_only``).

Summarisation for long results uses a user-supplied callable; the
default truncates to ``max_chars`` so the component is usable without
any LLM dependency.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-memory-components.md
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from llm_code.engine.component import component, output_types, state_writes
from llm_code.engine.components.memory.embedder import EmbedderComponent
from llm_code.engine.components.memory.schema import MemoryEntry, MemoryScope
from llm_code.engine.tracing import traced_component
from llm_code.memory.layer import MemoryLayer

__all__ = [
    "MemoryWriterComponent",
    "RememberFilter",
    "default_should_remember",
    "never_should_remember",
    "on_error_only",
    "non_read_only_only",
    "resolve_remember_filter",
]

_logger = logging.getLogger(__name__)
_DEFAULT_MAX_CHARS = 4000


# ---------------------------------------------------------------------------
# Policy predicates
# ---------------------------------------------------------------------------
RememberFilter = Callable[[str, Any, bool], bool]
"""``(tool_name, tool_result, is_error) -> bool`` — the writer calls this
to decide whether to persist a given tool outcome."""


def default_should_remember(
    tool_name: str, tool_result: Any, is_error: bool,
) -> bool:
    """Policy: ``always`` — persist every call."""
    _ = (tool_name, tool_result, is_error)
    return True


def never_should_remember(
    tool_name: str, tool_result: Any, is_error: bool,
) -> bool:
    """Policy: ``never`` — persist nothing. Handy for tests."""
    _ = (tool_name, tool_result, is_error)
    return False


def on_error_only(
    tool_name: str, tool_result: Any, is_error: bool,
) -> bool:
    """Policy: only persist failed calls (useful for post-mortem)."""
    _ = (tool_name, tool_result)
    return bool(is_error)


def non_read_only_only(
    tool_name: str, tool_result: Any, is_error: bool,
) -> bool:
    """Policy: skip pure-read tools (glob, grep, read_file...)."""
    _ = (tool_result, is_error)
    read_only = {"read_file", "glob_search", "grep_search", "ls", "git_diff", "git_log", "git_status"}
    return tool_name not in read_only


_POLICY_REGISTRY: dict[str, RememberFilter] = {
    "always": default_should_remember,
    "never": never_should_remember,
    "on_error_only": on_error_only,
    "non_read_only_only": non_read_only_only,
}


def resolve_remember_filter(
    policy: str | RememberFilter | None,
) -> RememberFilter:
    """Turn a policy name / callable / None into a callable filter.

    ``None`` → ``always``. Unknown names log a warning and fall back to
    ``always`` so a typo does not silently disable memory writes.
    """
    if callable(policy):
        return policy
    if policy is None:
        return default_should_remember
    fn = _POLICY_REGISTRY.get(policy)
    if fn is None:
        _logger.warning(
            "Unknown remember_filter policy %r; defaulting to 'always'", policy,
        )
        return default_should_remember
    return fn


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------
@traced_component
@component
@output_types(entry_id=str, written=bool)
@state_writes("memory_writes")
class MemoryWriterComponent:
    """Persist tool outcomes as memory entries.

    Args:
        layer: Storage backend. Held by reference.
        embedder: :class:`EmbedderComponent` used to vectorise the
            summarised result. Same instance should be reused for the
            query and the write path to avoid double model load.
        remember_filter: Policy predicate — see module docstring.
        summariser: Optional callable ``(tool_name, result) -> str``
            used for verbose results. Defaults to a length-clamped
            string cast.
        max_chars: Truncation boundary for the default summariser.
        default_scope: Scope to stamp on newly-written entries.

    Inputs:
        tool_call: Logical invocation descriptor; we only read
            ``tool_call.get("name")`` and ``tool_call.get("args", {})``
            so any mapping-shaped object works.
        tool_result: The object returned by the tool.
        is_error: Whether the call failed (for the policy predicate).
        scope: Optional scope override.

    Outputs:
        entry_id: The UUID of the written entry, or empty string if the
            policy skipped the write.
        written: ``True`` iff an entry was persisted.
    """

    concurrency_group = "io_bound"

    def __init__(
        self,
        layer: MemoryLayer,
        embedder: EmbedderComponent,
        *,
        remember_filter: str | RememberFilter | None = "always",
        summariser: Callable[[str, Any], str] | None = None,
        max_chars: int = _DEFAULT_MAX_CHARS,
        default_scope: MemoryScope = MemoryScope.PROJECT,
    ) -> None:
        self._layer = layer
        self._embedder = embedder
        self._should_remember: RememberFilter = resolve_remember_filter(remember_filter)
        self._summariser = summariser or _default_summariser
        self._max_chars = int(max_chars)
        self._default_scope = default_scope

    @property
    def layer(self) -> MemoryLayer:
        return self._layer

    def run(
        self,
        tool_call: dict[str, Any],
        tool_result: Any,
        is_error: bool = False,
        scope: MemoryScope | None = None,
    ) -> dict[str, Any]:
        tool_name = str(tool_call.get("name", "")) if isinstance(tool_call, dict) else ""
        if not self._should_remember(tool_name, tool_result, is_error):
            return {"entry_id": "", "written": False}

        effective_scope = scope if scope is not None else self._default_scope
        text = _clamp(self._summariser(tool_name, tool_result), self._max_chars)
        embed_out = self._embedder.run(text=text)
        embedding = tuple(embed_out["embedding"])
        entry_id = str(uuid.uuid4())
        entry = MemoryEntry(
            id=entry_id,
            text=text,
            scope=effective_scope,
            created_at=datetime.now(timezone.utc),
            embedding=embedding,
            source_tool=tool_name or None,
            metadata={"is_error": bool(is_error)},
        )
        self._layer.write(entry)
        return {"entry_id": entry_id, "written": True}

    async def run_async(
        self,
        tool_call: dict[str, Any],
        tool_result: Any,
        is_error: bool = False,
        scope: MemoryScope | None = None,
    ) -> dict[str, Any]:
        tool_name = str(tool_call.get("name", "")) if isinstance(tool_call, dict) else ""
        if not self._should_remember(tool_name, tool_result, is_error):
            return {"entry_id": "", "written": False}

        effective_scope = scope if scope is not None else self._default_scope
        text = _clamp(self._summariser(tool_name, tool_result), self._max_chars)
        embed_out = await self._embedder.run_async(text=text)
        embedding = tuple(embed_out["embedding"])
        entry_id = str(uuid.uuid4())
        entry = MemoryEntry(
            id=entry_id,
            text=text,
            scope=effective_scope,
            created_at=datetime.now(timezone.utc),
            embedding=embedding,
            source_tool=tool_name or None,
            metadata={"is_error": bool(is_error)},
        )
        await self._layer.write_async(entry)
        return {"entry_id": entry_id, "written": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _default_summariser(tool_name: str, result: Any) -> str:
    """Naive summariser — render result as a short string."""
    _ = tool_name
    if result is None:
        return ""
    try:
        return str(result)
    except Exception:
        return repr(result)


def _clamp(text: str, max_chars: int) -> str:
    """Cap ``text`` at ``max_chars`` without splitting inside UTF-8
    codepoints (Python slices strings by codepoint, so this is safe)."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


# Satisfy static analysis that ``Literal`` is considered used — the
# public type alias :data:`RememberFilter` only uses ``Callable``.
_ = Literal

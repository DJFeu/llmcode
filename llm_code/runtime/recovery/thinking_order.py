"""Wave2-1a: repair out-of-order ThinkingBlocks in an assistant Message.

Sibling of :mod:`llm_code.api.content_order`. That module *validates*
the invariant "all ThinkingBlocks must precede the first non-thinking
block" and raises on violation. This module *repairs* violations — it
is the recovery-layer counterpart used on ingestion paths where we
want best-effort healing rather than a hard failure:

* Streaming-assembly fallback when a provider interleaves reasoning
  chunks with text blocks out of order.
* DB rehydration of a legacy row from before the invariant was
  enforced at write time.
* Partial replay from a corrupted checkpoint.

Two repair modes are offered:

* ``"reorder"`` (default) — partition the blocks into thinking-first,
  non-thinking-second, preserving the relative order within each
  partition. The common provider bug (e.g. a late thinking delta
  arriving after a text block) is fixed by this. Thinking block
  *contents* and *signatures* are never modified — Anthropic extended
  thinking requires the payload to be echoed back verbatim, so moving
  a block but keeping its bytes intact is safe.
* ``"strip"`` — drop any ThinkingBlock that would have to move. This
  loses reasoning visibility but is the safest choice when the
  caller knows signatures are no longer valid (e.g. after editing an
  upstream message in a fork).

Both modes log a ``warning`` describing the repair so downstream
observers (audit logs, telemetry hooks) can notice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from llm_code.api.content_order import (
    ThinkingOrderError,
    validate_assistant_content_order,
)
from llm_code.api.types import ContentBlock, ThinkingBlock
from llm_code.logging import get_logger

logger = get_logger(__name__)

RepairMode = Literal["reorder", "strip"]


@dataclass(frozen=True)
class ThinkingOrderRepair:
    """Result of a :func:`repair_assistant_content_order` call.

    ``blocks`` is the repaired tuple (identity-preserved when no repair
    was needed — callers can ``if result.blocks is original`` to skip
    writes). ``changed`` makes the "did something happen?" check
    explicit without an identity comparison, and ``mode`` records which
    strategy produced the result. ``dropped`` is the number of
    ThinkingBlocks removed in strip mode (always 0 for reorder).
    """

    blocks: tuple[ContentBlock, ...]
    changed: bool
    mode: RepairMode
    dropped: int


def repair_assistant_content_order(
    blocks: tuple[ContentBlock, ...],
    *,
    mode: RepairMode = "reorder",
) -> ThinkingOrderRepair:
    """Return a repaired copy of ``blocks`` satisfying the invariant.

    If the tuple is already well-ordered, the original is returned
    unchanged (``changed=False``, ``blocks is original``). Otherwise:

    * ``mode="reorder"``: partition into (all ThinkingBlocks in original
      order, all non-thinking blocks in original order) and concatenate.
      This is the safe default because it preserves thinking payloads
      byte-for-byte — critical when the provider signed them.
    * ``mode="strip"``: drop every ThinkingBlock that lives after the
      first non-thinking block. Use this only when signatures are
      already invalid or the caller has no way to replay them.

    Empty tuples and tuples with zero thinking blocks always pass
    through unchanged.
    """
    # Fast path: invariant holds, return identity.
    try:
        validate_assistant_content_order(blocks)
    except ThinkingOrderError:
        pass
    else:
        return ThinkingOrderRepair(
            blocks=blocks, changed=False, mode=mode, dropped=0
        )

    if mode == "reorder":
        thinking: list[ContentBlock] = []
        other: list[ContentBlock] = []
        for block in blocks:
            if isinstance(block, ThinkingBlock):
                thinking.append(block)
            else:
                other.append(block)
        repaired = tuple(thinking) + tuple(other)
        logger.warning(
            "repair_assistant_content_order: reordered %d thinking block(s) "
            "to precede %d non-thinking block(s)",
            len(thinking),
            len(other),
        )
        return ThinkingOrderRepair(
            blocks=repaired, changed=True, mode="reorder", dropped=0
        )

    if mode == "strip":
        kept: list[ContentBlock] = []
        dropped = 0
        seen_non_thinking = False
        for block in blocks:
            if isinstance(block, ThinkingBlock):
                if seen_non_thinking:
                    dropped += 1
                    continue
            else:
                seen_non_thinking = True
            kept.append(block)
        repaired = tuple(kept)
        logger.warning(
            "repair_assistant_content_order: stripped %d late thinking block(s)",
            dropped,
        )
        return ThinkingOrderRepair(
            blocks=repaired, changed=True, mode="strip", dropped=dropped
        )

    raise ValueError(f"unknown repair mode: {mode!r}")

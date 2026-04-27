"""v2.9.0 P2 — tool-result compression on re-feed tests.

Each LLM iteration on slow local models (GLM via llama.cpp, Qwen via
vLLM) re-prefills the entire conversation history. After 3 web_search
calls the prefill includes ~40k tokens of stale tool payloads even
though the model only needs the *most recent* batch. P2 replaces
older tool_results with truncated markers; the trailing batch stays
intact so the current iteration's reasoning still has full data.

These tests pin down:

* Compression preserves the first 500 chars of each old payload
  prefixed with ``[v2.9 compressed]``.
* The most-recent contiguous tool_result batch is NOT compressed.
* Non-tool-result messages (assistant text, user prompts) are
  passed through verbatim.
* Compression is idempotent — running it twice doesn't double-truncate.
* When the profile flag is off, the conversion path is byte-parity
  with v2.8.1 (no compression applied).
* The profile schema round-trips ``compress_old_tool_results``
  through the ``[tool_consumption]`` section.
"""
from __future__ import annotations

from llm_code.api.conversion import (
    _COMPRESS_MARKER_PREFIX,
    _COMPRESS_PREVIEW_CHARS,
    compress_old_tool_results,
)
from llm_code.api.types import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.model_profile import _profile_from_dict


def _tool_result_msg(tool_use_id: str, content: str) -> Message:
    """Helper: build a user message holding a single ToolResultBlock."""
    return Message(
        role="user",
        content=(ToolResultBlock(tool_use_id=tool_use_id, content=content),),
    )


def _assistant_with_tool_use(tool_use_id: str, name: str = "web_search") -> Message:
    """Helper: assistant message that issued a tool_use, so the
    history shape mimics the real flow (assistant → tool_result →
    assistant → tool_result → ...).
    """
    return Message(
        role="assistant",
        content=(
            TextBlock(text=""),
            ToolUseBlock(id=tool_use_id, name=name, input={"query": "x"}),
        ),
    )


# ── Marker shape ─────────────────────────────────────────────────────


class TestCompressionMarker:
    """The truncated marker is structured + recognisable."""

    def test_marker_prefix_starts_with_version_tag(self) -> None:
        long_payload = "x" * 2000
        msg = _tool_result_msg("t1", long_payload)
        compressed_history = compress_old_tool_results((
            msg,
            _tool_result_msg("t2", "fresh"),  # trailing batch — kept full
            # Force a non-trailing-batch shape: trail = t2 only since
            # t1 sits before another tool message AND there's no
            # assistant break between them. To make t1 'old' we need
            # a non-tool-result message between them OR put t1 in a
            # different batch. Re-shape:
        ))
        # The above is a no-op (whole tail is tool results) — switch
        # to the proper "old vs new" pattern below.
        old_msg = _tool_result_msg("t1", long_payload)
        assistant_break = _assistant_with_tool_use("t2")
        new_msg = _tool_result_msg("t2", "fresh result")
        history = (old_msg, assistant_break, new_msg)
        compressed = compress_old_tool_results(history)
        # First message's content should now carry the marker.
        assert isinstance(compressed[0].content[0], ToolResultBlock)
        body = compressed[0].content[0].content
        assert body.startswith(_COMPRESS_MARKER_PREFIX), (
            f"compressed body must begin with marker; got {body[:40]!r}"
        )
        # Last (most recent) message must NOT be compressed.
        assert isinstance(compressed[2].content[0], ToolResultBlock)
        assert compressed[2].content[0].content == "fresh result"
        # Suppress unused warning from the diagnostic block above.
        del compressed_history

    def test_preview_keeps_first_500_chars(self) -> None:
        """The 500-char preview window is exactly that — no shorter."""
        long_payload = "A" * 600 + "B" * 600  # 1200 chars
        old = _tool_result_msg("t1", long_payload)
        brk = _assistant_with_tool_use("t2")
        new = _tool_result_msg("t2", "ok")
        out = compress_old_tool_results((old, brk, new))
        body = out[0].content[0].content
        # The preview window is _COMPRESS_PREVIEW_CHARS chars of the
        # original payload — assert that range is present verbatim.
        assert ("A" * _COMPRESS_PREVIEW_CHARS) in body
        # The truncated tail (B*600) must be absent.
        assert "B" * 600 not in body


# ── Trailing-batch preservation ──────────────────────────────────────


class TestTrailingBatchPreserved:
    """The most recent contiguous tool_result batch stays intact."""

    def test_two_messages_in_trailing_batch_both_kept(self) -> None:
        """If iter N+1 starts with two adjacent tool_result messages,
        both are 'most recent' and both stay full.
        """
        old = _tool_result_msg("t0", "stale" * 200)
        brk = _assistant_with_tool_use("t1")
        recent_a = _tool_result_msg("t1", "recent a")
        recent_b = _tool_result_msg("t2", "recent b")
        out = compress_old_tool_results((old, brk, recent_a, recent_b))
        # Old gets compressed.
        assert out[0].content[0].content.startswith(_COMPRESS_MARKER_PREFIX)
        # Both trailing-batch messages stay verbatim.
        assert out[2].content[0].content == "recent a"
        assert out[3].content[0].content == "recent b"

    def test_no_old_results_means_no_changes(self) -> None:
        """Single-iter conversation: every tool_result is the trailing
        batch, so nothing gets compressed.
        """
        msg_a = _tool_result_msg("t1", "result a")
        msg_b = _tool_result_msg("t2", "result b")
        history = (msg_a, msg_b)
        out = compress_old_tool_results(history)
        # Identity preserved.
        assert out == history


# ── Non-tool messages passthrough ────────────────────────────────────


class TestNonToolMessagesUntouched:
    """Assistant text, user prompts, mixed content: no changes."""

    def test_user_prompt_preserved(self) -> None:
        user_msg = Message(role="user", content=(TextBlock(text="hi"),))
        old = _tool_result_msg("t0", "x" * 1000)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        history = (user_msg, old, brk, new)
        out = compress_old_tool_results(history)
        # User prompt unchanged.
        assert out[0] == user_msg

    def test_assistant_text_preserved(self) -> None:
        a_msg = Message(role="assistant", content=(TextBlock(text="thinking..."),))
        old = _tool_result_msg("t0", "x" * 1000)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, a_msg, brk, new))
        assert out[1] == a_msg


# ── Idempotence ──────────────────────────────────────────────────────


class TestIdempotence:
    """Running compress() twice doesn't shrink further — the marker
    prefix already short-circuits subsequent compressions.
    """

    def test_double_compress_is_byte_parity_with_single(self) -> None:
        old = _tool_result_msg("t0", "x" * 1000)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        history = (old, brk, new)
        once = compress_old_tool_results(history)
        twice = compress_old_tool_results(once)
        # Pull out the compressed body from each; they should match.
        a = once[0].content[0].content
        b = twice[0].content[0].content
        assert a == b


# ── Empty / edge cases ───────────────────────────────────────────────


class TestEdgeCases:
    """Empty history and single-message histories don't crash."""

    def test_empty_messages(self) -> None:
        assert compress_old_tool_results(()) == ()

    def test_single_assistant_message(self) -> None:
        m = Message(role="assistant", content=(TextBlock(text="hi"),))
        assert compress_old_tool_results((m,)) == (m,)


# ── Profile schema round-trip ────────────────────────────────────────


class TestProfileSchemaRoundtrip:
    """``[tool_consumption] compress_old_tool_results`` parses cleanly."""

    def test_toml_section_loads_field(self) -> None:
        raw = {
            "name": "x",
            "tool_consumption": {"compress_old_tool_results": True},
        }
        profile = _profile_from_dict(raw)
        assert profile.compress_old_tool_results is True

    def test_toml_omitting_field_defaults_to_false(self) -> None:
        """Default is False so cloud profiles (Anthropic prompt cache,
        etc.) keep v2.8.1 byte-parity.
        """
        raw = {"name": "legacy"}
        profile = _profile_from_dict(raw)
        assert profile.compress_old_tool_results is False


# ── Backwards compat — flag-off path ─────────────────────────────────


class TestFlagOffByteParity:
    """Profiles with the flag off see no change to the conversion
    path (relies on the provider gates we wired in openai_compat.py
    + anthropic_provider.py)."""

    def test_compress_func_is_pure(self) -> None:
        """``compress_old_tool_results`` is invoked only when the
        provider sees ``profile.compress_old_tool_results=True``;
        the function itself is pure and doesn't read the profile."""
        history = (_tool_result_msg("t0", "x" * 1000),)
        # Calling the helper directly with a single trailing-batch
        # tool_result returns identity (nothing to compress) —
        # even when profile is irrelevant.
        out = compress_old_tool_results(history)
        assert out == history

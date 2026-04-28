"""v2.9.0 P2 / v2.13.0 Lever 3 — tool-result compression tests.

Each LLM iteration on slow local models (GLM via llama.cpp, Qwen via
vLLM) re-prefills the entire conversation history. After 3 web_search
calls the prefill includes ~40k tokens of stale tool payloads even
though the model only needs the *most recent* batch. P2 (v2.9.0)
replaces older tool_results with truncated markers; the trailing
batch stays intact so the current iteration's reasoning still has
full data. v2.13.0 Lever 3 tightens the preview cap (500 → 250
chars), bumps the marker prefix to ``[v2.13 compressed]`` while
still recognising the legacy ``[v2.9 compressed]`` marker for
backwards compatibility, and strips URL-list trailers from the
preview window.

These tests pin down:

* Compression preserves the first ``_COMPRESS_PREVIEW_CHARS`` chars
  of each old payload prefixed with the current marker tag.
* The most-recent contiguous tool_result batch is NOT compressed.
* Non-tool-result messages (assistant text, user prompts) are
  passed through verbatim.
* Compression is idempotent — running it twice doesn't double-truncate.
* The legacy v2.9 marker is also recognised by the idempotence
  check (backwards compat for long-running sessions that started
  on v2.9 and upgrade mid-conversation).
* URL-list trailers (``URL list: / * https://...``) are stripped
  from the preview window so the cap is spent on body content.
* When the profile flag is off, the conversion path is byte-parity
  with v2.8.1 (no compression applied).
* The profile schema round-trips ``compress_old_tool_results``
  through the ``[tool_consumption]`` section.
"""
from __future__ import annotations

from llm_code.api.conversion import (
    _COMPRESS_LEGACY_MARKER_PREFIX,
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

    def test_preview_keeps_first_n_chars(self) -> None:
        """The preview window is exactly ``_COMPRESS_PREVIEW_CHARS``
        wide — no shorter. v2.9.0 used 500; v2.13.0 Lever 3 tightens
        to 250. The test is parameterised on the constant so future
        cap changes don't require a rewrite.
        """
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


# ── v2.13.0 Lever 3 — backwards-compat for v2.9 marker ───────────────


class TestBackwardsCompatV29Marker:
    """v2.13's idempotence check still recognises the v2.9 marker.

    A long-running session whose history was compressed under v2.9
    must NOT have its already-compressed bodies re-compressed when
    the user upgrades to v2.13 mid-conversation. Re-compression
    would produce a v2.13 marker wrapping a v2.9 marker — visually
    confusing for log scrapers + wastes the wire payload on
    duplicate marker text.
    """

    def test_v29_marker_recognised_by_idempotence_check(self) -> None:
        v29_compressed_body = (
            f"{_COMPRESS_LEGACY_MARKER_PREFIX} preview "
            f"(500 chars of 1000):\n"
            f"some preview content...\n"
            f"[full content omitted ...]"
        )
        old = _tool_result_msg("t0", v29_compressed_body)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, brk, new))
        # The v2.9-format body should be preserved verbatim — no
        # re-compression under the v2.13 marker.
        assert out[0].content[0].content == v29_compressed_body

    def test_v213_marker_short_circuits_too(self) -> None:
        """v2.13 markers are also recognised (forward-compat against
        a future v2.14 marker bump that should preserve v2.13 too).
        """
        v213_compressed_body = (
            f"{_COMPRESS_MARKER_PREFIX} preview "
            f"({_COMPRESS_PREVIEW_CHARS} chars of 1000):\n"
            f"preview...\n"
            f"[full content omitted ...]"
        )
        old = _tool_result_msg("t0", v213_compressed_body)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, brk, new))
        assert out[0].content[0].content == v213_compressed_body


# ── v2.13.0 Lever 3 — URL-list trailer strip ────────────────────────


class TestUrlListStrip:
    """v2.13 strips structural URL-list trailers from the preview.

    Many search backends append a trailer of the form::

        URL list:
        * https://example.com/news/1
        * https://example.com/news/2

    These lines have no reasoning value once the body has been
    integrated. Stripping them spends the (now smaller) preview cap
    on body content. Conservative — body lines containing URLs
    alongside prose are preserved.
    """

    def test_url_list_trailer_removed_from_preview(self) -> None:
        # Build a payload whose first ~250 chars contain headlines
        # AND a URL-list trailer; pad past the cap with more body so
        # compression actually fires.
        body = (
            "Top news today\n"
            "1. Story A. First sentence about the news item.\n"
            "2. Story B. Another sentence describing the event.\n"
            "URL list:\n"
            "* https://example.com/news/1\n"
            "* https://example.com/news/2\n"
        )
        # Pad past _COMPRESS_PREVIEW_CHARS so compression actually
        # runs (idempotence path returns early when body fits).
        full_body = body + ("padding " * 200)
        old = _tool_result_msg("t0", full_body)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, brk, new))
        compressed = out[0].content[0].content
        # The marker should be present.
        assert compressed.startswith(_COMPRESS_MARKER_PREFIX)
        # Body content survives.
        assert "Top news today" in compressed
        # URL-list lines are stripped from the preview.
        assert "* https://example.com/news/1" not in compressed
        assert "* https://example.com/news/2" not in compressed
        # The "URL list:" header line is also dropped.
        assert "URL list:" not in compressed

    def test_inline_urls_preserved(self) -> None:
        """Body lines that mention URLs alongside prose stay intact —
        only entire-line URL bullets get stripped.
        """
        body = (
            "Article body — see https://example.com for the source.\n"
            "More body text continuing the explanation.\n"
        )
        full_body = body + ("padding " * 200)
        old = _tool_result_msg("t0", full_body)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, brk, new))
        compressed = out[0].content[0].content
        # Inline URL is preserved (it's in a body line with prose).
        assert "see https://example.com" in compressed

    def test_pure_url_list_payload_marker_still_emitted(self) -> None:
        """If the payload is dominated by URLs (no body prose), the
        compressor still emits the marker — this is the defensive
        path. The exact preview content depends on whether the last
        line in the truncated window is a complete URL match or a
        partial fragment; we assert only that the structural marker
        envelope is intact.
        """
        body = (
            "* https://example.com/a\n"
            "* https://example.com/b\n"
            "* https://example.com/c\n"
        )
        full_body = body + ("* https://example.com/d\n" * 100)
        old = _tool_result_msg("t0", full_body)
        brk = _assistant_with_tool_use("t1")
        new = _tool_result_msg("t1", "fresh")
        out = compress_old_tool_results((old, brk, new))
        compressed = out[0].content[0].content
        assert compressed.startswith(_COMPRESS_MARKER_PREFIX)
        assert "[full content omitted" in compressed


# ── v2.13.0 Lever 3 — preview cap shape ──────────────────────────────


class TestV213PreviewCap:
    """The v2.13 preview cap is exactly 250 chars — no looser, no
    tighter. Pinned as a constant so a future tune still passes.
    """

    def test_compress_preview_chars_is_250(self) -> None:
        assert _COMPRESS_PREVIEW_CHARS == 250

    def test_marker_prefix_is_v213(self) -> None:
        assert _COMPRESS_MARKER_PREFIX == "[v2.13 compressed]"

    def test_legacy_marker_prefix_is_v29(self) -> None:
        assert _COMPRESS_LEGACY_MARKER_PREFIX == "[v2.9 compressed]"

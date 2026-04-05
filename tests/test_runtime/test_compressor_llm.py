"""Tests for Level 5 LLM semantic compression in ContextCompressor."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from llm_code.api.types import Message, TextBlock, TokenUsage
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.session import Session


def _make_session(n_messages: int, chars_per_msg: int = 400) -> Session:
    msgs = []
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"Message {i}: " + "x" * chars_per_msg
        msgs.append(Message(role=role, content=(TextBlock(text=text),)))
    return Session(
        id="test1234",
        messages=tuple(msgs),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        total_usage=TokenUsage(input_tokens=0, output_tokens=0),
        project_path=Path("/tmp"),
    )


class TestLevel5LLMSummarize:
    @pytest.mark.asyncio
    async def test_compress_async_with_llm(self):
        """Level 5 replaces placeholder with LLM-generated summary."""
        response = MagicMock()
        response.content = "## Summary\nDid some coding.\n## Modified Files\n- /app/main.py"
        response.usage = TokenUsage(input_tokens=100, output_tokens=50)
        provider = AsyncMock()
        provider.complete.return_value = response

        compressor = ContextCompressor(
            max_result_chars=100,
            provider=provider,
            summarize_model="test-model",
        )
        session = _make_session(40, chars_per_msg=1000)
        result = await compressor.compress_async(session, max_tokens=2000)
        assert provider.complete.called
        # Find summary message
        found_summary = False
        for msg in result.messages:
            for block in msg.content:
                if isinstance(block, TextBlock) and "Did some coding" in block.text:
                    found_summary = True
        assert found_summary

    @pytest.mark.asyncio
    async def test_compress_async_fallback_on_error(self):
        """If LLM call fails, fall back to Level 4 placeholder."""
        provider = AsyncMock()
        provider.complete.side_effect = Exception("API error")
        compressor = ContextCompressor(
            max_result_chars=100,
            provider=provider,
            summarize_model="test-model",
        )
        session = _make_session(40, chars_per_msg=1000)
        result = await compressor.compress_async(session, max_tokens=2000)
        assert len(result.messages) > 0
        # Should have placeholder (fallback)
        has_placeholder = any(
            isinstance(b, TextBlock) and "Previous conversation summary" in b.text
            for msg in result.messages for b in msg.content
        )
        assert has_placeholder

    @pytest.mark.asyncio
    async def test_compress_async_no_provider_skips_level5(self):
        """Without a provider, compress_async behaves like sync compress."""
        compressor = ContextCompressor(max_result_chars=100)
        session = _make_session(40, chars_per_msg=1000)
        result = await compressor.compress_async(session, max_tokens=2000)
        has_placeholder = any(
            isinstance(b, TextBlock) and "Previous conversation summary" in b.text
            for msg in result.messages for b in msg.content
        )
        assert has_placeholder

    def test_sync_compress_unchanged(self):
        """Sync compress() should NOT use Level 5."""
        compressor = ContextCompressor(max_result_chars=100)
        session = _make_session(40, chars_per_msg=1000)
        result = compressor.compress(session, max_tokens=2000)
        assert len(result.messages) > 0

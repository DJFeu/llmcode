"""M2: prompt-cache boundary message for compaction."""
from __future__ import annotations


class TestBoundary:
    def test_build_boundary_with_ephemeral_cache_control(self) -> None:
        from llm_code.runtime.compact_boundaries import (
            build_boundary_message,
        )
        msg = build_boundary_message(
            summary="40 earlier messages summarised.",
            previous_msg_count=40,
        )
        assert msg["role"] == "user"
        # Content blocks carry the cache_control hint.
        contents = msg["content"]
        assert isinstance(contents, list)
        marker = contents[0]
        assert "Previous conversation summary" in marker["text"]
        assert marker.get("cache_control") == {"type": "ephemeral"}

    def test_boundary_carries_previous_count(self) -> None:
        from llm_code.runtime.compact_boundaries import (
            build_boundary_message,
        )
        msg = build_boundary_message(summary="ok", previous_msg_count=42)
        joined = msg["content"][0]["text"]
        assert "42" in joined

    def test_boundary_omits_cache_when_disabled(self) -> None:
        from llm_code.runtime.compact_boundaries import (
            build_boundary_message,
        )
        msg = build_boundary_message(
            summary="ok", previous_msg_count=10, cache_control=False,
        )
        assert "cache_control" not in msg["content"][0]

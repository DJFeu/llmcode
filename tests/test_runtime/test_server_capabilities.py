"""Tests for the persistent server_capabilities cache.

This cache exists so the 14-second native-tool-call rejection
latency (observed on Qwen3.5-122B via vLLM without
``--enable-auto-tool-choice``) is paid ONCE per server+model
combo, ever — not once per session.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime import server_capabilities


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the cache file to a tmp path so tests don't touch
    the user's real ~/.llmcode/server_capabilities.json."""
    monkeypatch.setattr(
        server_capabilities, "_CACHE_PATH", tmp_path / "server_capabilities.json"
    )
    yield


def test_load_returns_none_when_cache_does_not_exist() -> None:
    """First ever call on a fresh system: no cache file → None.
    The runtime treats None as 'go ahead and try native mode and
    let the fallback branch discover the answer'."""
    assert server_capabilities.load_native_tools_support(
        base_url="http://localhost:8000", model="qwen3",
    ) is None


def test_mark_then_load_returns_false() -> None:
    """After a mark_native_tools_unsupported call, the cache
    should report False for the same key."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://localhost:8000", model="qwen3",
    )
    assert server_capabilities.load_native_tools_support(
        base_url="http://localhost:8000", model="qwen3",
    ) is False


def test_different_model_has_independent_entry() -> None:
    """A user running two models on the same server must get
    independent cache entries — one bad model doesn't poison
    a sibling model's native support."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://localhost:8000", model="qwen3",
    )
    # Sibling model on same server — still unknown (not cached)
    assert server_capabilities.load_native_tools_support(
        base_url="http://localhost:8000", model="llama3",
    ) is None


def test_different_base_url_has_independent_entry() -> None:
    """Same model on different servers is independent."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://server-a:8000", model="qwen3",
    )
    assert server_capabilities.load_native_tools_support(
        base_url="http://server-b:8000", model="qwen3",
    ) is None


def test_trailing_slash_normalized() -> None:
    """``http://x:8000/`` and ``http://x:8000`` are the same
    server for caching purposes."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://localhost:8000/", model="qwen3",
    )
    assert server_capabilities.load_native_tools_support(
        base_url="http://localhost:8000", model="qwen3",
    ) is False


def test_mark_preserves_other_entries() -> None:
    """Marking one server+model does not clobber other entries."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://a:8000", model="qwen3",
    )
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://b:8000", model="llama3",
    )
    assert server_capabilities.load_native_tools_support("http://a:8000", "qwen3") is False
    assert server_capabilities.load_native_tools_support("http://b:8000", "llama3") is False


def test_corrupted_cache_returns_none(tmp_path: Path) -> None:
    """A malformed JSON file should not crash; treat as 'no cache'."""
    (tmp_path / "server_capabilities.json").write_text("not valid json", encoding="utf-8")
    assert server_capabilities.load_native_tools_support(
        base_url="http://localhost:8000", model="qwen3",
    ) is None


def test_cache_entry_has_cached_at_timestamp(tmp_path: Path) -> None:
    """Each write must include a timestamp so a future
    retention-policy feature can expire old entries."""
    server_capabilities.mark_native_tools_unsupported(
        base_url="http://localhost:8000", model="qwen3",
    )
    data = json.loads((tmp_path / "server_capabilities.json").read_text())
    key = next(iter(data.keys()))
    assert "cached_at" in data[key]
    assert "2026" in data[key]["cached_at"]  # ISO format includes year


def test_clear_removes_specific_entry() -> None:
    server_capabilities.mark_native_tools_unsupported("http://a", "qwen3")
    server_capabilities.mark_native_tools_unsupported("http://b", "llama3")
    server_capabilities.clear_native_tools_cache("http://a", "qwen3")
    assert server_capabilities.load_native_tools_support("http://a", "qwen3") is None
    assert server_capabilities.load_native_tools_support("http://b", "llama3") is False


def test_clear_wipes_entire_cache_when_called_with_no_args() -> None:
    server_capabilities.mark_native_tools_unsupported("http://a", "qwen3")
    server_capabilities.mark_native_tools_unsupported("http://b", "llama3")
    server_capabilities.clear_native_tools_cache()
    assert server_capabilities.load_native_tools_support("http://a", "qwen3") is None
    assert server_capabilities.load_native_tools_support("http://b", "llama3") is None


def test_clear_with_only_one_arg_raises() -> None:
    """Partial clear is ambiguous — must pass both or neither."""
    with pytest.raises(ValueError, match="both or neither"):
        server_capabilities.clear_native_tools_cache(base_url="http://a")


def test_write_is_atomic_no_tmp_files_left() -> None:
    """Successful writes should not leave .tmp files littering
    the cache directory."""
    server_capabilities.mark_native_tools_unsupported("http://a", "qwen3")
    cache_dir = server_capabilities._CACHE_PATH.parent
    tmp_files = list(cache_dir.glob(".server_capabilities.*.tmp"))
    assert tmp_files == []


def test_conversation_runtime_seeds_force_xml_from_cache_source() -> None:
    """Source-level guard: the runtime's ``_run_turn_body`` must
    call ``load_native_tools_support`` when setting up
    ``self._force_xml_mode`` so the cache is actually consulted."""
    import inspect
    from llm_code.runtime.conversation import ConversationRuntime
    src = inspect.getsource(ConversationRuntime._run_turn_body)
    assert "load_native_tools_support" in src


def test_conversation_runtime_writes_cache_on_fallback_source() -> None:
    """Source-level guard: the XML-fallback branch must call
    ``mark_native_tools_unsupported`` so the NEXT session loads
    the cached answer and skips native mode entirely."""
    import inspect
    from llm_code.runtime.conversation import ConversationRuntime
    src = inspect.getsource(ConversationRuntime._run_turn_body)
    assert "mark_native_tools_unsupported" in src

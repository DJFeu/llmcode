"""Flag-only wiring for the (Wave C) StreamingToolExecutor.

The turn-loop ordering contract between model stream events and
tool-result events is intricate enough that we keep the existing serial
path as the default. This test locks in the opt-in config flag so a
future change can wire the new executor in without silently switching
every session over.

TODO(v1.10): actually route tool dispatch through
llm_code.runtime.streaming_tool_executor.StreamingToolExecutor when the
flag is True, after the ordering invariants are validated end-to-end.
"""
from __future__ import annotations

from llm_code.runtime.config import RuntimeConfig
from llm_code.runtime.streaming_tool_executor import (
    StreamingToolExecutor,
    ToolCall,
    is_concurrent_safe,
)


def test_use_streaming_tool_executor_flag_defaults_false():
    cfg = RuntimeConfig()
    assert hasattr(cfg, "use_streaming_tool_executor")
    assert cfg.use_streaming_tool_executor is False


def test_streaming_tool_executor_is_importable_and_constructible():
    ex = StreamingToolExecutor(max_concurrent=2)
    assert ex.max_concurrent == 2


def test_safety_class_separation():
    assert is_concurrent_safe("read_file")
    assert is_concurrent_safe("grep_search")
    assert not is_concurrent_safe("edit_file")
    assert not is_concurrent_safe("bash")


def test_tool_call_dataclass():
    c = ToolCall(id="t1", name="read_file", arguments={"path": "a"})
    assert c.name == "read_file"
    assert c.arguments["path"] == "a"

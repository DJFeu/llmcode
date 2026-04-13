"""Wiring tests for QueryProfiler into conversation runtime + /profile cmd."""
from __future__ import annotations

import inspect

from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.query_profiler import QueryProfiler


def test_runtime_has_query_profiler_init():
    src = inspect.getsource(ConversationRuntime.__init__)
    assert "QueryProfiler" in src
    assert "_query_profiler" in src


def test_runtime_records_usage_on_stop_event():
    src = inspect.getsource(ConversationRuntime)
    assert "_query_profiler.record" in src


def test_profile_command_registered():
    from llm_code.cli.commands import COMMAND_REGISTRY
    names = {c.name for c in COMMAND_REGISTRY}
    assert "profile" in names
    profile_cmd = next(c for c in COMMAND_REGISTRY if c.name == "profile")
    assert profile_cmd.no_arg is True


def test_app_has_cmd_profile_handler():
    from llm_code.view.dispatcher import CommandDispatcher
    assert hasattr(CommandDispatcher, "_cmd_profile")


def test_query_profiler_format_breakdown_empty():
    p = QueryProfiler()
    out = p.format_breakdown()
    assert "no API calls yet" in out


def test_query_profiler_records_and_formats():
    p = QueryProfiler()

    class _Usage:
        input_tokens = 1000
        output_tokens = 500

    p.record(model="gpt-4o", usage_block=_Usage())
    p.record(model="gpt-4o", usage_block=_Usage())
    out = p.format_breakdown()
    assert "gpt-4o" in out
    assert "2 calls" in out

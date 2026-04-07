"""Tests for QueryProfiler — per-model token + cost tracking."""
from __future__ import annotations

from llm_code.api.types import TokenUsage
from llm_code.runtime.query_profiler import ModelProfile, QueryProfiler


def test_record_single_call():
    p = QueryProfiler()
    p.record("claude-sonnet-4-6", TokenUsage(input_tokens=1000, output_tokens=200))
    breakdown = p.per_model_breakdown()
    assert len(breakdown) == 1
    assert breakdown[0].input_tokens == 1000
    assert breakdown[0].output_tokens == 200
    assert breakdown[0].call_count == 1


def test_record_multiple_calls_accumulates():
    p = QueryProfiler()
    p.record("gpt-4o", TokenUsage(input_tokens=100, output_tokens=10))
    p.record("gpt-4o", TokenUsage(input_tokens=200, output_tokens=20))
    [prof] = p.per_model_breakdown()
    assert prof.input_tokens == 300
    assert prof.output_tokens == 30
    assert prof.call_count == 2


def test_record_multiple_models():
    p = QueryProfiler()
    p.record("gpt-4o", TokenUsage(input_tokens=100, output_tokens=10))
    p.record("claude-sonnet-4-6", TokenUsage(input_tokens=1000, output_tokens=100))
    p.record("claude-sonnet-4-6", TokenUsage(input_tokens=500, output_tokens=50))
    out = p.per_model_breakdown()
    assert len(out) == 2
    # sorted by call_count desc
    assert out[0].model == "claude-sonnet-4-6"
    assert out[0].call_count == 2


def test_record_dict_usage():
    p = QueryProfiler()
    p.record("gpt-4o", {"prompt_tokens": 50, "completion_tokens": 5})
    [prof] = p.per_model_breakdown()
    assert prof.input_tokens == 50
    assert prof.output_tokens == 5


def test_cache_token_tracking():
    p = QueryProfiler()
    p.record(
        "claude-sonnet-4-6",
        {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 2000},
    )
    [prof] = p.per_model_breakdown()
    assert prof.cache_read_tokens == 5000
    assert prof.cache_write_tokens == 2000


def test_total_cost_uses_builtin_pricing():
    p = QueryProfiler()
    # claude-sonnet-4-6 is (3.00, 15.00) per million
    p.record("claude-sonnet-4-6", TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000))
    cost = p.total_cost()
    assert abs(cost - (3.0 + 15.0)) < 0.01


def test_total_cost_local_model_free():
    p = QueryProfiler()
    p.record("qwen3-122b-local", TokenUsage(input_tokens=10_000, output_tokens=1_000))
    assert p.total_cost() == 0.0


def test_total_cost_custom_table():
    p = QueryProfiler()
    p.record("my-model", TokenUsage(input_tokens=1_000_000, output_tokens=0))
    table = {"my-model": [4.0, 8.0]}
    assert abs(p.total_cost(table) - 4.0) < 0.001


def test_format_breakdown_empty():
    p = QueryProfiler()
    out = p.format_breakdown()
    assert "no API calls" in out


def test_format_breakdown_with_data():
    p = QueryProfiler()
    p.record("claude-sonnet-4-6", TokenUsage(input_tokens=28_000, output_tokens=4_000))
    p.record("qwen3-local", TokenUsage(input_tokens=45_000, output_tokens=1_000))
    out = p.format_breakdown()
    assert "claude-sonnet-4-6" in out
    assert "qwen3-local" in out
    assert "Total:" in out
    assert "(local)" in out


def test_model_profile_dataclass_defaults():
    mp = ModelProfile(model="x")
    assert mp.input_tokens == 0
    assert mp.call_count == 0

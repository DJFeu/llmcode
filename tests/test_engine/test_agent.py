"""Tests for :class:`llm_code.engine.agent.Agent` and
:func:`llm_code.engine.agent.build_agent_from_config`.

All tests use a ``MagicMock(spec=Pipeline)`` plus a lambda ``chat_fn``;
they never import any concrete Component (M2 subagent is building those
in parallel; we must stay decoupled).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from unittest.mock import MagicMock

import pytest

from llm_code.engine.agent import (
    Agent,
    _extract_tool_result,
    _get_attr,
    _is_denied,
    _with_args,
    _with_tool,
    build_agent_from_config,
)
from llm_code.engine.agent_result import AgentError, AgentResult
from llm_code.engine.pipeline import Pipeline
from llm_code.engine.policies import (
    DegradedDecision,
    FallbackDecision,
    RetryDecision,
)
from llm_code.engine.policies.budget import RetryBudget
from llm_code.engine.policies.degraded import (
    ConsecutiveFailureDegraded,
    NoDegraded,
)
from llm_code.engine.policies.exit import (
    CompositeExit,
    DenialThreshold,
    ExplicitExitTool,
    MaxStepsReached,
)
from llm_code.engine.policies.fallback import NoFallback, SemanticFallback
from llm_code.engine.policies.retry import (
    ExponentialBackoff,
    NoRetry,
    RetryOnRateLimit,
)
from llm_code.runtime.config import AgentLoopConfig, EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _ToolCall:
    """Minimal tool-call DTO used throughout these tests."""

    id: str
    tool_name: str
    args: dict = field(default_factory=dict)

    def with_args(self, args: dict) -> "_ToolCall":
        return replace(self, args=dict(args))

    def with_tool(self, tool_name: str) -> "_ToolCall":
        return replace(self, tool_name=tool_name)


def _mock_pipeline(result=None, raise_exc=None, tools: tuple = ()):
    """Build a ``MagicMock(spec=Pipeline)`` configured to return a
    canonical ``{"post": {"formatted_result": ...}}`` dict or raise.
    """
    pipeline = MagicMock(spec=Pipeline)
    pipeline.tools = tools
    if raise_exc is not None:
        pipeline.run.side_effect = raise_exc
    else:
        pipeline.run.return_value = {"post": {"formatted_result": result}}
    return pipeline


class _ChatScript:
    """Scripted ``chat_fn`` that yields pre-recorded responses.

    Each call to the agent's chat_fn pops the next ``(tool_calls, text)``
    pair. Past the end of the script the fn returns ``([], [])`` so
    the agent exits cleanly.
    """

    def __init__(self, script: list):
        self._script = list(script)
        self.calls: list[tuple] = []

    def __call__(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if not self._script:
            return [], []
        return self._script.pop(0)


class _SimpleResult:
    """Tool-result stand-in — content + is_error."""

    def __init__(self, content: str, is_error: bool = False):
        self.content = content
        self.is_error = is_error


# ---------------------------------------------------------------------------
# Construction + defaults
# ---------------------------------------------------------------------------


class TestAgentConstruction:
    def test_default_policies(self):
        pipeline = _mock_pipeline(result=None)
        agent = Agent(pipeline, chat_fn=lambda m, t: ([], []))
        assert isinstance(agent._retry, NoRetry)
        assert isinstance(agent._fallback, NoFallback)
        assert isinstance(agent._degraded, NoDegraded)
        assert isinstance(agent._exit, CompositeExit)

    def test_validates_max_steps(self):
        with pytest.raises(ValueError):
            Agent(_mock_pipeline(), chat_fn=lambda m, t: ([], []), max_agent_steps=0)

    def test_custom_retry_budget(self):
        budget = RetryBudget(max_total_retries=5)
        agent = Agent(
            _mock_pipeline(), chat_fn=lambda m, t: ([], []), retry_budget=budget
        )
        assert agent._budget is budget

    def test_default_exit_uses_max_agent_steps(self):
        agent = Agent(
            _mock_pipeline(), chat_fn=lambda m, t: ([], []), max_agent_steps=7
        )
        # Reach into the default member to confirm cap wiring.
        inner = agent._exit.members[0]
        assert isinstance(inner, MaxStepsReached)
        assert inner.cap == 7

    def test_custom_sleep_fn(self):
        calls = []
        agent = Agent(
            _mock_pipeline(),
            chat_fn=lambda m, t: ([], []),
            sleep_fn=calls.append,
        )
        agent._sleep(0.25)
        assert calls == [0.25]


# ---------------------------------------------------------------------------
# Clean runs (no tools, no errors)
# ---------------------------------------------------------------------------


class TestCleanRuns:
    def test_no_tool_calls_exits_immediately(self):
        pipeline = _mock_pipeline()
        script = _ChatScript([([], ["Hello"])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user", "content": "hi"}])
        assert isinstance(result, AgentResult)
        assert result.exit_reason == "model_responded"
        assert result.iterations == 0
        assert result.retries_used == 0

    def test_assistant_text_captured_in_messages(self):
        pipeline = _mock_pipeline()
        script = _ChatScript([([], ["Hello, world!"])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user", "content": "hi"}])
        assert result.final_text == "Hello, world!"
        roles = [m.get("role") for m in result.messages]
        assert "assistant" in roles

    def test_text_chunks_as_dicts(self):
        pipeline = _mock_pipeline()
        script = _ChatScript([([], [{"text": "chunk1"}, {"text": "chunk2"}])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user", "content": "hi"}])
        assert result.final_text == "chunk1chunk2"

    def test_single_tool_call_then_response(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tc = _ToolCall(id="1", tool_name="read_file", args={"path": "/x"})
        script = _ChatScript([([tc], []), ([], ["Done"])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user", "content": "read x"}])
        assert result.exit_reason == "model_responded"
        assert result.iterations == 1
        assert len(result.tool_results) == 1
        # Tool role message injected after the tool call
        assert any(m.get("role") == "tool" for m in result.messages)

    def test_multiple_tool_calls_in_single_turn(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tcs = [_ToolCall(id=str(i), tool_name="x", args={}) for i in range(3)]
        script = _ChatScript([(tcs, []), ([], ["done"])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user", "content": "x"}])
        # Each call results in a tool-result entry
        assert len(result.tool_results) == 3

    def test_messages_argument_not_mutated(self):
        pipeline = _mock_pipeline()
        script = _ChatScript([([], ["Hello"])])
        agent = Agent(pipeline, chat_fn=script)
        original = [{"role": "user", "content": "hi"}]
        agent.run(original)
        assert original == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Max steps + warning reminder
# ---------------------------------------------------------------------------


class TestMaxSteps:
    def test_agent_exits_at_cap(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        # Always return a tool call → loop until max_steps trips.
        tc = _ToolCall(id="1", tool_name="read_file")

        def chat(m, t):
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            max_agent_steps=3,
            exit_conditions=[MaxStepsReached(cap=3, warning_offset=0)],
        )
        result = agent.run([{"role": "user"}])
        assert "max_steps" in result.exit_reason
        assert result.iterations == 3

    def test_warning_reminder_injected_once_at_cap_minus_offset(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tc = _ToolCall(id="1", tool_name="read_file")

        def chat(m, t):
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            exit_conditions=[MaxStepsReached(cap=5, warning_offset=2)],
            max_agent_steps=5,
        )
        result = agent.run([{"role": "user"}])
        sys_reminders = [
            m for m in result.messages
            if m.get("role") == "system" and "steps" in str(m.get("content", ""))
        ]
        # Emitted at iteration 3 only (cap 5 − offset 2)
        assert len(sys_reminders) == 1

    def test_warning_reminder_not_emitted_below_threshold(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tc = _ToolCall(id="1", tool_name="x")

        call_count = {"n": 0}

        def chat(m, t):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return [tc], []
            return [], ["done"]

        agent = Agent(
            pipeline,
            chat_fn=chat,
            exit_conditions=[MaxStepsReached(cap=100, warning_offset=5)],
            max_agent_steps=100,
        )
        result = agent.run([{"role": "user"}])
        sys_reminders = [
            m for m in result.messages
            if m.get("role") == "system" and "steps remain" in str(m.get("content", ""))
        ]
        assert sys_reminders == []


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class _CountingPipeline:
    """Pipeline stand-in that raises on the first N runs then succeeds."""

    def __init__(self, fails: int, exc_factory=lambda: ConnectionResetError()):
        self.tools = ()
        self._fails = fails
        self._exc_factory = exc_factory
        self.calls = 0

    def run(self, inputs):
        self.calls += 1
        if self.calls <= self._fails:
            raise self._exc_factory()
        return {"post": {"formatted_result": _SimpleResult("ok")}}


class TestRetry:
    def test_retries_transient_then_succeeds(self):
        pipeline = _CountingPipeline(fails=2)
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        sleeps: list[float] = []
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=ExponentialBackoff(max_attempts=5, base_ms=10, cap_ms=100),
            sleep_fn=sleeps.append,
        )
        result = agent.run([{"role": "user"}])
        assert result.exit_reason == "model_responded"
        assert result.retries_used == 2  # two retries consumed
        assert pipeline.calls == 3       # 2 fails + 1 success
        assert len(sleeps) == 2          # slept between retries

    def test_non_transient_surfaces_error(self):
        pipeline = _CountingPipeline(fails=5, exc_factory=lambda: ValueError("bad"))
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=ExponentialBackoff(max_attempts=5),
        )
        result = agent.run([{"role": "user"}])
        assert result.retries_used == 0
        tool_result = result.tool_results[0]
        assert isinstance(tool_result, AgentError)
        assert "failed" in tool_result.content

    def test_retry_exhausted_surfaces_error(self):
        pipeline = _CountingPipeline(fails=100)
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=ExponentialBackoff(max_attempts=2, base_ms=1),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        tool_result = result.tool_results[0]
        assert isinstance(tool_result, AgentError)

    def test_rate_limit_retry(self):
        class _Err(Exception):
            pass

        err_instance = _Err()
        err_instance.retry_after_seconds = 0  # immediate

        class _FlakePipeline:
            def __init__(self):
                self.tools = ()
                self.n = 0

            def run(self, _):
                self.n += 1
                if self.n == 1:
                    raise err_instance
                return {"post": {"formatted_result": _SimpleResult("ok")}}

        pipeline = _FlakePipeline()
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        # Name-duck type: _Err contains no 'ratelimit' string, so rely
        # on status_code-based detection.
        err_instance.status_code = 429
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=RetryOnRateLimit(max_attempts=3, default_delay_ms=0),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        assert result.retries_used == 1

    def test_retry_modified_args(self):
        pipeline = _CountingPipeline(fails=1)
        tc = _ToolCall(id="1", tool_name="x", args={"q": 1})
        script = _ChatScript([([tc], []), ([], ["done"])])

        class _MutatingRetry:
            def should_retry(self, err, attempt, state):
                return RetryDecision(
                    should_retry=True,
                    delay_ms=0,
                    modified_args={"q": 2},
                    reason="mutate",
                )

        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=_MutatingRetry(),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        assert result.retries_used == 1


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------


class TestFallback:
    def test_semantic_fallback_swaps_tool(self):
        class _NamedPipeline:
            def __init__(self):
                self.tools = ()
                self.seen_tools: list[str] = []

            def run(self, inputs):
                tc = inputs["exec"]["tool_call"]
                self.seen_tools.append(tc.tool_name)
                if tc.tool_name == "web_search":
                    raise RuntimeError("search down")
                return {"post": {"formatted_result": _SimpleResult("got it")}}

        pipeline = _NamedPipeline()
        tc = _ToolCall(id="1", tool_name="web_search")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            fallback_policy=SemanticFallback(),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        # First call on web_search, fallback to web_fetch, succeeds.
        assert pipeline.seen_tools == ["web_search", "web_fetch"]
        assert result.retries_used == 1  # fallback consumes budget
        # The tool result in the state must not be an error
        assert not getattr(result.tool_results[0], "is_error", True)

    def test_no_fallback_declared_surfaces_error(self):
        pipeline = _CountingPipeline(fails=100, exc_factory=lambda: RuntimeError("boom"))
        tc = _ToolCall(id="1", tool_name="unknown_tool")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            fallback_policy=SemanticFallback(),
        )
        result = agent.run([{"role": "user"}])
        tool_result = result.tool_results[0]
        assert isinstance(tool_result, AgentError)

    def test_retry_first_then_fallback(self):
        """When retry is exhausted without success, fallback kicks in."""
        class _StagedPipeline:
            def __init__(self):
                self.tools = ()
                self.seen: list[str] = []

            def run(self, inputs):
                tc = inputs["exec"]["tool_call"]
                self.seen.append(tc.tool_name)
                if tc.tool_name == "web_search":
                    raise ConnectionResetError("transient")
                return {"post": {"formatted_result": _SimpleResult("ok")}}

        pipeline = _StagedPipeline()
        tc = _ToolCall(id="1", tool_name="web_search")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=ExponentialBackoff(max_attempts=2, base_ms=1, cap_ms=1),
            fallback_policy=SemanticFallback(),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        # Initial call + 2 retries = 3 web_search calls, then 1 fallback
        # to web_fetch (which succeeds).
        assert pipeline.seen.count("web_search") == 3
        assert pipeline.seen[-1] == "web_fetch"
        # 2 retries consumed + 1 fallback swap consumes budget
        assert result.retries_used == 2 + 1


# ---------------------------------------------------------------------------
# Degraded mode
# ---------------------------------------------------------------------------


class TestDegraded:
    def test_degrades_after_consecutive_failures(self):
        """Three consecutive failed tool calls → switch to read-only."""
        call_history: list[str] = []

        class _AlwaysFailPipeline:
            def __init__(self):
                # Tools objects — named, for filter check.
                self.tools = (
                    type("_T", (), {"name": "read_file"})(),
                    type("_T", (), {"name": "write_file"})(),
                )

            def run(self, inputs):
                call_history.append(inputs["exec"]["tool_call"].tool_name)
                raise RuntimeError("always bad")

        pipeline = _AlwaysFailPipeline()
        tc = _ToolCall(id="1", tool_name="write_file")

        def chat(messages, tools):
            if len(call_history) >= 3:
                return [], ["done"]
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            degraded_policy=ConsecutiveFailureDegraded(threshold=3),
        )
        result = agent.run([{"role": "user"}])
        assert result.degraded is True

    def test_system_message_announces_degraded(self):
        class _AlwaysFail:
            def __init__(self):
                self.tools = ()

            def run(self, inputs):
                raise RuntimeError("bad")

        pipeline = _AlwaysFail()
        tc = _ToolCall(id="1", tool_name="x")
        turns = {"n": 0}

        def chat(m, t):
            turns["n"] += 1
            if turns["n"] > 4:
                return [], ["done"]
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            degraded_policy=ConsecutiveFailureDegraded(threshold=2),
        )
        result = agent.run([{"role": "user"}])
        system_msgs = [
            m for m in result.messages
            if m.get("role") == "system"
            and "read-only mode" in str(m.get("content", ""))
        ]
        assert len(system_msgs) == 1  # only once

    def test_degraded_filters_tools(self):
        read_only = type("_T", (), {"name": "read_file"})()
        write_only = type("_T", (), {"name": "write_file"})()

        class _Pipeline:
            tools = (read_only, write_only)

            def run(self, inputs):
                return {"post": {"formatted_result": _SimpleResult("ok")}}

        pipeline = _Pipeline()
        turns = {"n": 0}
        seen_tools: list[list[str]] = []

        def chat(messages, tools):
            turns["n"] += 1
            seen_tools.append([getattr(t, "name", "?") for t in tools])
            if turns["n"] < 2:
                return [_ToolCall(id="1", tool_name="read_file")], []
            return [], ["done"]

        class _ForceDegrade:
            def __init__(self):
                self._hit = False

            def check(self, state):
                if not self._hit:
                    self._hit = True
                    return DegradedDecision(
                        should_degrade=True,
                        allowed_tools=frozenset({"read_file"}),
                        reason="manual trigger",
                    )
                return DegradedDecision(should_degrade=False)

        agent = Agent(
            pipeline,
            chat_fn=chat,
            degraded_policy=_ForceDegrade(),
        )
        result = agent.run([{"role": "user"}])
        # First turn saw both tools? No — degrade check runs before chat.
        assert seen_tools[0] == ["read_file"]
        assert result.degraded is True


# ---------------------------------------------------------------------------
# Denial + exit conditions
# ---------------------------------------------------------------------------


class TestDenialExit:
    def test_denial_threshold_exits(self):
        class _DenyPipeline:
            tools = ()

            def run(self, inputs):
                return {
                    "post": {
                        "formatted_result": _SimpleResult(
                            "permission denied for tool", is_error=True
                        )
                    }
                }

        pipeline = _DenyPipeline()
        tc = _ToolCall(id="1", tool_name="x")

        def chat(m, t):
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            exit_conditions=[
                DenialThreshold(threshold=2, window=10),
                MaxStepsReached(cap=50, warning_offset=100),
            ],
        )
        result = agent.run([{"role": "user"}])
        assert "denial" in result.exit_reason.lower()

    def test_explicit_exit_tool(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tc = _ToolCall(id="1", tool_name="exit_agent")
        script = _ChatScript([([tc], [])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            exit_conditions=[ExplicitExitTool(), MaxStepsReached(cap=50, warning_offset=100)],
        )
        result = agent.run([{"role": "user"}])
        assert "explicit exit" in result.exit_reason


# ---------------------------------------------------------------------------
# Retry budget (infinite-loop prevention)
# ---------------------------------------------------------------------------


class TestRetryBudgetIntegration:
    def test_budget_prevents_infinite_loop(self):
        """Adversarial: retry always says yes, fallback always proposes swap.

        Without the budget, this would loop forever. With a small budget
        the agent surfaces an error after budget exhaustion.
        """
        class _AlwaysRetry:
            def should_retry(self, error, attempt, state):
                return RetryDecision(should_retry=True, delay_ms=0, reason="always")

        class _SwapFallback:
            def fallback(self, failed_tool, error, state):
                return FallbackDecision(
                    fallback_tool="other_tool", reason="always swap"
                )

        class _AlwaysFailPipeline:
            tools = ()

            def run(self, inputs):
                raise RuntimeError("always")

        pipeline = _AlwaysFailPipeline()
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=_AlwaysRetry(),
            fallback_policy=_SwapFallback(),
            retry_budget=RetryBudget(max_total_retries=5),
            sleep_fn=lambda _: None,
        )
        result = agent.run([{"role": "user"}])
        # Must terminate — budget stopped the loop
        assert result.retries_used == 5
        tool_result = result.tool_results[0]
        assert isinstance(tool_result, AgentError)

    def test_zero_budget_surfaces_error_immediately(self):
        pipeline = _CountingPipeline(fails=1)
        tc = _ToolCall(id="1", tool_name="x")
        script = _ChatScript([([tc], []), ([], ["done"])])
        agent = Agent(
            pipeline,
            chat_fn=script,
            retry_policy=ExponentialBackoff(max_attempts=5, base_ms=1),
            retry_budget=RetryBudget(max_total_retries=0),
        )
        result = agent.run([{"role": "user"}])
        assert result.retries_used == 0
        assert isinstance(result.tool_results[0], AgentError)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_attr_object(self):
        tc = _ToolCall(id="1", tool_name="x")
        assert _get_attr(tc, "tool_name") == "x"

    def test_get_attr_mapping(self):
        assert _get_attr({"tool_name": "x"}, "tool_name") == "x"

    def test_get_attr_default(self):
        assert _get_attr({}, "missing", "fallback") == "fallback"

    def test_with_args_on_dataclass(self):
        tc = _ToolCall(id="1", tool_name="x", args={"a": 1})
        new = _with_args(tc, {"b": 2})
        assert new.args == {"b": 2}
        assert tc.args == {"a": 1}  # original untouched

    def test_with_args_on_dict(self):
        d = {"tool_name": "x", "args": {"a": 1}}
        new = _with_args(d, {"b": 2})
        assert new["args"] == {"b": 2}

    def test_with_tool_on_dataclass(self):
        tc = _ToolCall(id="1", tool_name="x")
        new = _with_tool(tc, "y")
        assert new.tool_name == "y"

    def test_with_tool_on_dict(self):
        d = {"tool_name": "x"}
        new = _with_tool(d, "y")
        assert new["tool_name"] == "y"

    def test_with_args_fails_on_unknown_shape(self):
        with pytest.raises(TypeError):
            _with_args("not_an_object", {"a": 1})

    def test_extract_tool_result_uses_post(self):
        out = _extract_tool_result({"post": {"formatted_result": "x"}}, None)
        assert out == "x"

    def test_extract_tool_result_falls_through(self):
        out = _extract_tool_result({"other": {"formatted_result": "y"}}, None)
        assert out == "y"

    def test_extract_tool_result_returns_raw_on_unknown(self):
        out = _extract_tool_result("raw", None)
        assert out == "raw"

    def test_is_denied_true(self):
        assert _is_denied(_SimpleResult("Permission Denied: nope", True)) is True

    def test_is_denied_false(self):
        assert _is_denied(_SimpleResult("no worries", False)) is False


# ---------------------------------------------------------------------------
# build_agent_from_config
# ---------------------------------------------------------------------------


class TestBuildAgentFromConfig:
    def test_defaults_are_safe(self):
        cfg = AgentLoopConfig()
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent, Agent)
        assert isinstance(agent._retry, NoRetry)
        assert isinstance(agent._fallback, NoFallback)

    def test_exponential_retry_selected(self):
        cfg = AgentLoopConfig(retry_policy="exponential", retry_max_attempts=5)
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent._retry, ExponentialBackoff)

    def test_rate_limit_retry(self):
        cfg = AgentLoopConfig(retry_policy="rate_limit")
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent._retry, RetryOnRateLimit)

    def test_unknown_retry_rejected(self):
        cfg = AgentLoopConfig(retry_policy="banana")
        with pytest.raises(ValueError, match="unknown retry_policy"):
            build_agent_from_config(
                cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
            )

    def test_semantic_fallback(self):
        cfg = AgentLoopConfig(fallback_policy="semantic")
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent._fallback, SemanticFallback)

    def test_model_fallback_requires_suggest_fn(self):
        cfg = AgentLoopConfig(fallback_policy="model")
        with pytest.raises(ValueError, match="suggest_fn"):
            build_agent_from_config(
                cfg,
                _mock_pipeline(),
                chat_fn=lambda m, t: ([], []),
            )

    def test_model_fallback_with_suggest_fn(self):
        from llm_code.engine.policies.fallback import ModelFallback
        cfg = AgentLoopConfig(fallback_policy="model")
        agent = build_agent_from_config(
            cfg,
            _mock_pipeline(),
            chat_fn=lambda m, t: ([], []),
            fallback_tools=("a", "b"),
            suggest_fn=lambda f, e, tools: "a",
        )
        assert isinstance(agent._fallback, ModelFallback)

    def test_consecutive_failure_degraded(self):
        cfg = AgentLoopConfig(
            degraded_policy="consecutive_failure", degraded_threshold=5
        )
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent._degraded, ConsecutiveFailureDegraded)

    def test_budget_degraded_requires_usage_fn(self):
        cfg = AgentLoopConfig(degraded_policy="budget")
        with pytest.raises(ValueError, match="usage_fn"):
            build_agent_from_config(
                cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
            )

    def test_custom_exit_conditions(self):
        cfg = AgentLoopConfig(exit_conditions=("max_steps", "no_progress"))
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert len(agent._exit.members) == 2

    def test_unknown_exit_condition_rejected(self):
        cfg = AgentLoopConfig(exit_conditions=("max_steps", "banana"))
        with pytest.raises(ValueError, match="unknown exit"):
            build_agent_from_config(
                cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
            )

    def test_budget_exhausted_requires_usage_fn(self):
        cfg = AgentLoopConfig(exit_conditions=("budget_exhausted",))
        with pytest.raises(ValueError, match="budget_exhausted"):
            build_agent_from_config(
                cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
            )

    def test_none_config_uses_defaults(self):
        """``agent_cfg`` can be ``None`` — defaults fall through via getattr."""
        agent = build_agent_from_config(
            None, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent, Agent)

    def test_retry_budget_wired(self):
        cfg = AgentLoopConfig(retry_budget=7)
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert agent._budget.max_total_retries == 7

    def test_max_steps_wired(self):
        cfg = AgentLoopConfig(max_agent_steps=13)
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        # Default exit list becomes MaxStepsReached(cap=13)
        ms = agent._exit.members[0]
        assert ms.cap == 13

    def test_composite_retry_policy(self):
        from llm_code.engine.policies.retry import CompositeRetryPolicy
        cfg = AgentLoopConfig(retry_policy="composite")
        agent = build_agent_from_config(
            cfg, _mock_pipeline(), chat_fn=lambda m, t: ([], [])
        )
        assert isinstance(agent._retry, CompositeRetryPolicy)


# ---------------------------------------------------------------------------
# AgentResult shape
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_frozen(self):
        r = AgentResult(messages=[], exit_reason="done")
        with pytest.raises(Exception):
            r.exit_reason = "changed"  # type: ignore[misc]

    def test_error_frozen(self):
        e = AgentError(content="x")
        with pytest.raises(Exception):
            e.content = "y"  # type: ignore[misc]

    def test_default_is_error_true(self):
        assert AgentError(content="x").is_error is True

    def test_final_text_populated_on_clean_run(self):
        pipeline = _mock_pipeline()
        script = _ChatScript([([], ["final answer"])])
        agent = Agent(pipeline, chat_fn=script)
        result = agent.run([{"role": "user"}])
        assert result.final_text == "final answer"

    def test_final_text_empty_on_max_steps(self):
        pipeline = _mock_pipeline(result=_SimpleResult("ok"))
        tc = _ToolCall(id="1", tool_name="x")

        def chat(m, t):
            return [tc], []

        agent = Agent(
            pipeline,
            chat_fn=chat,
            max_agent_steps=2,
            exit_conditions=[MaxStepsReached(cap=2, warning_offset=0)],
        )
        result = agent.run([{"role": "user"}])
        assert result.final_text == ""


# ---------------------------------------------------------------------------
# Integration with the conversation shim
# ---------------------------------------------------------------------------


class TestConversationShim:
    """Verify the :func:`llm_code.runtime.conversation.run_conversation`
    entry point wires everything together.

    Post-v2.0 (M8.b) there is no legacy branch and no ``legacy_fn``
    kwarg — both entry points unconditionally drive the engine Agent.
    """

    def test_sync_runs_agent(self):
        from llm_code.runtime.conversation import run_conversation

        pipeline = _mock_pipeline()
        script = _ChatScript([([], ["Hello"])])
        cfg = type("_Cfg", (), {"engine": EngineConfig()})()
        result = run_conversation(
            [{"role": "user", "content": "hi"}],
            config=cfg,
            pipeline=pipeline,
            chat_fn=script,
        )
        assert isinstance(result, AgentResult)

    def test_sync_handles_missing_engine_config(self):
        """Callers that do not build an ``EngineConfig`` (older test
        fixtures) must still succeed — the entry point falls back to
        agent defaults."""
        from llm_code.runtime.conversation import run_conversation

        cfg = type("_Cfg", (), {})()  # no .engine attr at all
        result = run_conversation(
            [{"role": "user", "content": "hi"}],
            config=cfg,
            pipeline=_mock_pipeline(),
            chat_fn=_ChatScript([([], ["direct"])]),
        )
        assert isinstance(result, AgentResult)

"""Tests for /analyze and /diff_check slash commands in the TUI app."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llm_code.tui.app import LLMCodeTUI
from llm_code.tui.chat_view import ChatScrollView


class TestAnalyzeCommandAttrs:
    """Unit tests — verify handler methods exist and app initializes correctly."""

    def test_cmd_analyze_exists(self) -> None:
        app = LLMCodeTUI()
        assert hasattr(app._cmd_dispatcher, "_cmd_analyze")
        assert callable(app._cmd_dispatcher._cmd_analyze)

    def test_cmd_diff_check_exists(self) -> None:
        app = LLMCodeTUI()
        assert hasattr(app._cmd_dispatcher, "_cmd_diff_check")
        assert callable(app._cmd_dispatcher._cmd_diff_check)

    def test_analysis_context_initialized_none(self) -> None:
        app = LLMCodeTUI()
        assert app._analysis_context is None

    def test_run_analyze_method_exists(self) -> None:
        app = LLMCodeTUI()
        assert hasattr(app._cmd_dispatcher, "_run_analyze")
        assert asyncio.iscoroutinefunction(app._cmd_dispatcher._run_analyze)

    def test_run_diff_check_method_exists(self) -> None:
        app = LLMCodeTUI()
        assert hasattr(app._cmd_dispatcher, "_run_diff_check")
        assert asyncio.iscoroutinefunction(app._cmd_dispatcher._run_diff_check)


class TestAnalyzeCommandRegistration:
    """Verify /analyze and /diff_check are in KNOWN_COMMANDS."""

    def test_analyze_in_known_commands(self) -> None:
        from llm_code.cli.commands import KNOWN_COMMANDS
        assert "analyze" in KNOWN_COMMANDS

    def test_diff_check_in_known_commands(self) -> None:
        from llm_code.cli.commands import KNOWN_COMMANDS
        assert "diff_check" in KNOWN_COMMANDS

    def test_parse_analyze_command(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/analyze")
        assert cmd is not None
        assert cmd.name == "analyze"
        assert cmd.args == ""

    def test_parse_analyze_with_path(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/analyze src/")
        assert cmd is not None
        assert cmd.name == "analyze"
        assert cmd.args == "src/"

    def test_parse_diff_check_command(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/diff_check")
        assert cmd is not None
        assert cmd.name == "diff_check"
        assert cmd.args == ""


@pytest.mark.asyncio
async def test_run_analyze_no_violations(tmp_path: Path) -> None:
    """_run_analyze with a clean directory shows 'No violations found' in chat."""
    from llm_code.analysis.rules import AnalysisResult

    app = LLMCodeTUI(cwd=tmp_path)

    clean_result = AnalysisResult(
        violations=(),
        file_count=2,
        duration_ms=10.0,
    )

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_analysis",
            return_value=clean_result,
        ):
            await app._cmd_dispatcher._run_analyze("")

        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "No violations found" in combined

        # No violations → analysis_context cleared
        assert app._analysis_context is None


@pytest.mark.asyncio
async def test_run_analyze_with_violations(tmp_path: Path) -> None:
    """_run_analyze with violations stores analysis context and shows results."""
    from llm_code.analysis.rules import AnalysisResult, Violation

    violation = Violation(
        rule_key="bare-except",
        severity="high",
        file_path="src/api.py",
        line=88,
        message="Bare except clause",
    )
    result = AnalysisResult(
        violations=(violation,),
        file_count=5,
        duration_ms=25.0,
    )

    app = LLMCodeTUI(cwd=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_analysis",
            return_value=result,
        ):
            await app._cmd_dispatcher._run_analyze("")

        await pilot.pause()

        # Analysis context should be populated
        assert app._analysis_context is not None
        assert "bare-except" in app._analysis_context or "Bare except" in app._analysis_context

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "src/api.py" in combined or "bare-except" in combined.lower()


@pytest.mark.asyncio
async def test_run_analyze_propagates_to_runtime(tmp_path: Path) -> None:
    """_run_analyze sets analysis_context on the runtime when violations exist."""
    from llm_code.analysis.rules import AnalysisResult, Violation

    violation = Violation(
        rule_key="print-in-prod",
        severity="medium",
        file_path="main.py",
        line=10,
        message="print() in production code",
    )
    result = AnalysisResult(
        violations=(violation,),
        file_count=1,
        duration_ms=5.0,
    )

    app = LLMCodeTUI(cwd=tmp_path)

    # Attach a mock runtime
    mock_runtime = MagicMock()
    mock_runtime.analysis_context = None
    app._runtime = mock_runtime

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_analysis",
            return_value=result,
        ):
            await app._cmd_dispatcher._run_analyze("")

        await pilot.pause()

    # Runtime should have analysis_context set
    assert mock_runtime.analysis_context is not None


@pytest.mark.asyncio
async def test_run_analyze_clears_runtime_on_no_violations(tmp_path: Path) -> None:
    """_run_analyze clears runtime.analysis_context when no violations found."""
    from llm_code.analysis.rules import AnalysisResult

    clean_result = AnalysisResult(
        violations=(),
        file_count=3,
        duration_ms=8.0,
    )

    app = LLMCodeTUI(cwd=tmp_path)
    mock_runtime = MagicMock()
    mock_runtime.analysis_context = "old context"
    app._runtime = mock_runtime

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_analysis",
            return_value=clean_result,
        ):
            await app._cmd_dispatcher._run_analyze("")

        await pilot.pause()

    assert mock_runtime.analysis_context is None


@pytest.mark.asyncio
async def test_run_analyze_handles_exception(tmp_path: Path) -> None:
    """_run_analyze shows error in chat if run_analysis raises."""
    app = LLMCodeTUI(cwd=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_analysis",
            side_effect=RuntimeError("disk error"),
        ):
            await app._cmd_dispatcher._run_analyze("")

        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "Analysis failed" in combined or "disk error" in combined


@pytest.mark.asyncio
async def test_run_diff_check_no_changes(tmp_path: Path) -> None:
    """_run_diff_check shows 'No changes' when both lists are empty."""
    app = LLMCodeTUI(cwd=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_diff_check",
            return_value=([], []),
        ):
            await app._cmd_dispatcher._run_diff_check("")

        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "No changes" in combined


@pytest.mark.asyncio
async def test_run_diff_check_new_and_fixed(tmp_path: Path) -> None:
    """_run_diff_check shows NEW and FIXED lines correctly."""
    from llm_code.analysis.rules import Violation

    new_v = Violation(
        rule_key="bare-except",
        severity="high",
        file_path="src/api.py",
        line=88,
        message="Bare except clause",
    )
    fixed_v = Violation(
        rule_key="empty-except",
        severity="medium",
        file_path="src/utils.py",
        line=30,
        message="Empty except body",
    )

    app = LLMCodeTUI(cwd=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_diff_check",
            return_value=([new_v], [fixed_v]),
        ):
            await app._cmd_dispatcher._run_diff_check("")

        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "NEW" in combined
        assert "FIXED" in combined
        assert "src/api.py" in combined
        assert "src/utils.py" in combined


@pytest.mark.asyncio
async def test_run_diff_check_handles_exception(tmp_path: Path) -> None:
    """_run_diff_check shows error in chat if run_diff_check raises."""
    app = LLMCodeTUI(cwd=tmp_path)

    async with app.run_test(size=(120, 40)) as pilot:
        with patch(
            "llm_code.analysis.engine.run_diff_check",
            side_effect=RuntimeError("git not found"),
        ):
            await app._cmd_dispatcher._run_diff_check("")

        await pilot.pause()

        chat = app.query_one(ChatScrollView)
        texts = [entry._text for entry in chat.query("AssistantText")]
        combined = " ".join(texts)
        assert "Diff check failed" in combined or "git not found" in combined


class TestAnalysisContextInjection:
    """Verify ConversationRuntime has analysis_context attribute."""

    def test_runtime_has_analysis_context_attr(self) -> None:
        """ConversationRuntime initializes analysis_context to None."""
        from unittest.mock import MagicMock
        from llm_code.runtime.conversation import ConversationRuntime

        mock_config = MagicMock()
        mock_config.max_tokens = 4096
        mock_config.max_turn_iterations = 10
        mock_config.hida = None
        mock_config.model = "test"
        mock_config.temperature = 0.0
        mock_config.thinking = MagicMock()

        runtime = ConversationRuntime(
            provider=MagicMock(),
            tool_registry=MagicMock(),
            permission_policy=MagicMock(),
            hook_runner=MagicMock(),
            prompt_builder=MagicMock(),
            config=mock_config,
            session=MagicMock(),
            context=MagicMock(),
        )

        assert hasattr(runtime, "analysis_context")
        assert runtime.analysis_context is None

    def test_runtime_analysis_context_settable(self) -> None:
        """analysis_context can be set on ConversationRuntime."""
        from unittest.mock import MagicMock
        from llm_code.runtime.conversation import ConversationRuntime

        mock_config = MagicMock()
        mock_config.max_tokens = 4096
        mock_config.max_turn_iterations = 10
        mock_config.hida = None
        mock_config.model = "test"
        mock_config.temperature = 0.0
        mock_config.thinking = MagicMock()

        runtime = ConversationRuntime(
            provider=MagicMock(),
            tool_registry=MagicMock(),
            permission_policy=MagicMock(),
            hook_runner=MagicMock(),
            prompt_builder=MagicMock(),
            config=mock_config,
            session=MagicMock(),
            context=MagicMock(),
        )

        ctx = "[Code Analysis] 3 violations found:\n- HIGH src/api.py:88 Bare except"
        runtime.analysis_context = ctx
        assert runtime.analysis_context == ctx

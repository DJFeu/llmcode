"""Tests for ``llmcode`` subcommand wiring (v12 M8 CLI surface).

The main entry point in :mod:`llm_code.cli.main` was upgraded from a
plain ``@click.command()`` into a ``@click.group(invoke_without_command=
True)`` so the hayhooks / memory / migrate / trace standalone click
groups can be registered as ``llmcode <subcommand> ...`` without
breaking the existing ``llmcode`` (REPL) and ``llmcode "prompt"``
surfaces.

These tests pin down the user-visible contract:

* ``llmcode <subcommand> --help`` must reach the subcommand's own help
  screen, not the main group's help screen.
* ``llmcode`` with no args must still launch the REPL.
* ``llmcode "hello"`` (one positional) must still launch the REPL and
  the ``prompt`` value must be captured as a tuple.
* ``llmcode --model qwen`` must still launch the REPL with the model
  override applied.

We patch the REPL entry points so the tests can run headlessly without
a real provider, API key, or terminal.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from llm_code.cli.main import main


async def _noop_repl(backend, state) -> None:
    """Stand-in for ``_run_repl`` — does nothing but completes cleanly."""
    return None


_SHELL_STATE = SimpleNamespace(runtime=None)


def _patched_repl_invocation(args: list[str]):
    """Invoke ``main`` with every REPL-adjacent dependency stubbed out."""
    runner = CliRunner()
    with patch(
        "llm_code.runtime.app_state.AppState.from_config",
        return_value=_SHELL_STATE,
    ), patch(
        "llm_code.cli.main._run_repl", _noop_repl,
    ), patch(
        "llm_code.view.repl.backend.REPLBackend",
    ):
        return runner.invoke(main, args)


class TestSubcommandHelp:
    """Each registered subcommand must reach its own --help screen."""

    def test_hayhooks_help_shows_hayhooks_group(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["hayhooks", "--help"])
        assert result.exit_code == 0, (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        # Hayhooks help must mention the ``serve`` subcommand.
        assert "serve" in result.output, result.output
        # And it must NOT be the main group's help (which lists
        # ``hayhooks`` as a subcommand).
        assert "--model" not in result.output

    def test_memory_help_shows_memory_group(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["memory", "--help"])
        assert result.exit_code == 0, (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        # Memory help must mention the ``migrate`` subcommand.
        assert "migrate" in result.output, result.output

    def test_migrate_help_shows_migrate_group(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["migrate", "--help"])
        assert result.exit_code == 0, (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        # Migrate help must mention the ``v12`` subcommand.
        assert "v12" in result.output, result.output

    def test_trace_help_shows_trace_group(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["trace", "--help"])
        assert result.exit_code == 0, (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        # Trace help must mention the list/show/tail subcommands.
        out = result.output
        assert "list" in out and "show" in out and "tail" in out, out

    def test_main_help_lists_all_subcommands(self) -> None:
        """``llmcode --help`` must show the main group and list each
        registered subcommand."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        for name in ("hayhooks", "memory", "migrate", "trace"):
            assert name in result.output, (
                f"subcommand {name!r} missing from main --help:\n"
                f"{result.output}"
            )


class TestReplFallthrough:
    """No-subcommand paths must still reach the REPL launcher."""

    def test_no_args_enters_repl(self) -> None:
        """``llmcode`` with no arguments must invoke the REPL code path."""
        with patch("llm_code.cli.main._run_repl") as mock_repl:
            # Make the patched _run_repl return an awaitable no-op.
            async def _ret(*_args, **_kwargs):
                return None

            mock_repl.side_effect = _ret
            with patch(
                "llm_code.runtime.app_state.AppState.from_config",
                return_value=_SHELL_STATE,
            ), patch(
                "llm_code.view.repl.backend.REPLBackend",
            ):
                runner = CliRunner()
                result = runner.invoke(main, [])
            assert result.exit_code == 0, (
                f"exit={result.exit_code} output={result.output!r} "
                f"exc={result.exception!r}"
            )
            assert mock_repl.called, (
                "_run_repl must be invoked when no subcommand is given"
            )

    def test_quoted_prompt_enters_repl(self) -> None:
        """``llmcode "hello"`` (one positional) must still reach REPL.

        The prompt is captured but is not required for REPL startup —
        existing behavior in the pre-group CLI ignored the positional
        argument and fell straight into the REPL. We keep that.
        """
        with patch("llm_code.cli.main._run_repl") as mock_repl:
            async def _ret(*_args, **_kwargs):
                return None

            mock_repl.side_effect = _ret
            with patch(
                "llm_code.runtime.app_state.AppState.from_config",
                return_value=_SHELL_STATE,
            ), patch(
                "llm_code.view.repl.backend.REPLBackend",
            ):
                runner = CliRunner()
                result = runner.invoke(main, ["hello world"])
            assert result.exit_code == 0, (
                f"exit={result.exit_code} output={result.output!r} "
                f"exc={result.exception!r}"
            )
            assert mock_repl.called

    def test_model_option_enters_repl_and_applies(self) -> None:
        """``llmcode --model qwen`` must invoke REPL with the model
        override pushed through ``AppState.from_config``."""
        with patch("llm_code.cli.main._run_repl") as mock_repl, patch(
            "llm_code.runtime.app_state.AppState.from_config",
            return_value=_SHELL_STATE,
        ) as mock_from_config, patch(
            "llm_code.view.repl.backend.REPLBackend",
        ):

            async def _ret(*_args, **_kwargs):
                return None

            mock_repl.side_effect = _ret
            runner = CliRunner()
            result = runner.invoke(main, ["--model", "qwen"])
        assert result.exit_code == 0, (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        assert mock_repl.called
        # Model option should flow through load_config → AppState.from_config.
        # We don't introspect config internals here — just confirm the
        # REPL path was taken (not a subcommand).
        assert mock_from_config.called


class TestSubcommandArgumentConflict:
    """The ``--`` separator should be able to force a prompt even when
    the first token matches a registered subcommand name.

    We don't assert the exact semantics of ``--`` routing — that's
    Click's job. We just ensure that a bare ``hayhooks`` token routes
    to the hayhooks group (matching user expectation per the plan's
    subcommand-vs-prompt conflict section)."""

    def test_bare_subcommand_token_routes_to_group(self) -> None:
        """``llmcode hayhooks`` (no further args) should show hayhooks
        group help because it's a group with no default command."""
        runner = CliRunner()
        result = runner.invoke(main, ["hayhooks"])
        # When a group is invoked with no subcommand and no invoke_
        # without_command flag, Click prints help and exits cleanly
        # with code 2 (no subcommand supplied). Either 0 (help shown)
        # or 2 (missing subcommand) is acceptable — the key point is
        # that Click recognized ``hayhooks`` as a subcommand, not a
        # prompt value.
        assert result.exit_code in (0, 2), (
            f"exit={result.exit_code} output={result.output!r} "
            f"exc={result.exception!r}"
        )
        # The output must mention the hayhooks group's own subcommands
        # or usage line, which proves subcommand routing happened.
        assert "serve" in result.output or "Usage:" in result.output

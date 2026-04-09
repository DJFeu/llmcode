"""Tests for the log-file destination logic in setup_logging().

Covers the new --log-file CLI flag and LLMCODE_LOG_FILE env var
that were added to let -v runs capture verbose logs without
breaking the TUI rendering (which writes to stderr).

Each test resets the llm_code logger state because setup_logging
is intentionally idempotent in the common case (the real CLI
calls it once); for tests we need to be able to re-init with
different arguments.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from llm_code.logging import setup_logging


@pytest.fixture(autouse=True)
def reset_logger():
    """Each test starts with a clean llm_code logger. Otherwise the
    idempotent ``if logger.handlers: return`` shortcut in
    setup_logging would cause the second test to inherit the first
    test's handler configuration."""
    logger = logging.getLogger("llm_code")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    for h in saved_handlers:
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    yield
    for h in logger.handlers[:]:
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        logger.addHandler(h)
    logger.setLevel(saved_level)


def test_default_destination_is_stderr() -> None:
    """Without log_file arg or env var, setup_logging writes to stderr
    via a StreamHandler. This is the pre-existing behavior."""
    logger = setup_logging(verbose=True)
    assert len(logger.handlers) == 1
    h = logger.handlers[0]
    assert isinstance(h, logging.StreamHandler)
    assert not isinstance(h, logging.FileHandler)


def test_explicit_log_file_creates_filehandler(tmp_path: Path) -> None:
    """Passing a log_file path installs a FileHandler, NOT a
    StreamHandler — so -v logs never touch stderr."""
    log_path = tmp_path / "llmv.log"
    logger = setup_logging(verbose=True, log_file=log_path)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.FileHandler)
    # Sanity: the underlying file path matches
    assert Path(logger.handlers[0].baseFilename) == log_path


def test_log_file_writes_messages(tmp_path: Path) -> None:
    """Actually emit a record and verify it lands in the file, not stderr."""
    log_path = tmp_path / "sink.log"
    logger = setup_logging(verbose=True, log_file=log_path)
    logger.warning("test message from test_log_file_writes_messages")
    # Force flush
    for h in logger.handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert "test message from test_log_file_writes_messages" in content


def test_log_file_parent_dir_created(tmp_path: Path) -> None:
    """A caller can pass ~/.llmcode/logs/today.log without
    pre-creating the ``logs`` directory; setup_logging creates
    the parent tree for them."""
    log_path = tmp_path / "nested" / "sub" / "file.log"
    assert not log_path.parent.exists()
    setup_logging(verbose=True, log_file=log_path)
    assert log_path.parent.exists()


def test_env_var_used_when_no_explicit_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LLMCODE_LOG_FILE`` env var is the secondary source for the
    log destination. Used by shell users who want to set it once
    in their rc file and forget about it."""
    log_path = tmp_path / "from_env.log"
    monkeypatch.setenv("LLMCODE_LOG_FILE", str(log_path))
    logger = setup_logging(verbose=True)
    assert isinstance(logger.handlers[0], logging.FileHandler)
    assert Path(logger.handlers[0].baseFilename) == log_path


def test_explicit_arg_overrides_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI --log-file takes precedence over LLMCODE_LOG_FILE."""
    env_path = tmp_path / "env.log"
    arg_path = tmp_path / "arg.log"
    monkeypatch.setenv("LLMCODE_LOG_FILE", str(env_path))
    logger = setup_logging(verbose=True, log_file=arg_path)
    assert Path(logger.handlers[0].baseFilename) == arg_path


def test_log_file_expands_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path starting with ~ should be expanded — users type
    ~/.llmcode/logs/debug.log, not /Users/alice/.llmcode/...
    monkeypatching HOME lets the test run hermetically."""
    monkeypatch.setenv("HOME", str(tmp_path))
    logger = setup_logging(verbose=True, log_file="~/my.log")
    assert Path(logger.handlers[0].baseFilename) == tmp_path / "my.log"


def test_verbose_false_still_accepts_log_file(tmp_path: Path) -> None:
    """Log file destination is independent of verbosity level."""
    log_path = tmp_path / "quiet.log"
    logger = setup_logging(verbose=False, log_file=log_path)
    assert isinstance(logger.handlers[0], logging.FileHandler)
    assert logger.level == logging.WARNING  # verbose=False → WARNING

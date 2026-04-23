"""Structured logging for llm-code."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def setup_logging(
    verbose: bool = False,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Configure root llm_code logger. Safe to call multiple times.

    Destination priority:
      1. Explicit ``log_file`` argument (CLI flag ``--log-file PATH``)
      2. ``LLMCODE_LOG_FILE`` environment variable
      3. Default: ``sys.stderr``

    When a log file is used the StreamHandler is NOT added, so the
    TUI's stderr stream is never polluted. This matters because
    ``-v 2> /tmp/log`` would otherwise interleave Python logging
    output with Textual's own stderr writes, which completely
    breaks the TUI rendering for anyone who wants to capture a
    verbose log during a live session.
    """
    logger = logging.getLogger("llm_code")
    if logger.handlers:
        return logger

    level = logging.DEBUG if verbose else logging.WARNING

    # Resolve log file destination. Explicit arg wins; then env var.
    resolved_log_file = log_file or os.environ.get("LLMCODE_LOG_FILE")

    if resolved_log_file:
        path = Path(resolved_log_file).expanduser()
        # Create parent directory if it doesn't exist so the caller
        # can pass ~/.llmcode/logs/today.log without pre-creating it.
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(
            path, mode="a", encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``llm_code`` namespace.

    Callers typically pass ``__name__``. For modules inside the
    ``llm_code`` package the dotted name already starts with
    ``llm_code.`` — re-prefixing produces ``llm_code.llm_code.view…``
    style double-prefixed logger names that show up in WARNING output
    (observed on v2.2.2 glm-5.1 stream_renderer warnings). Skip the
    prefix when the caller already provided a package-qualified name.
    """
    if name == "llm_code" or name.startswith("llm_code."):
        return logging.getLogger(name)
    return logging.getLogger(f"llm_code.{name}")

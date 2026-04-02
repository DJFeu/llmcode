"""Structured logging for llm-code."""
from __future__ import annotations

import logging
import sys


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure root llm_code logger. Safe to call multiple times."""
    logger = logging.getLogger("llm_code")
    if logger.handlers:
        return logger

    level = logging.DEBUG if verbose else logging.WARNING
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
    """Return a child logger under the llm_code namespace."""
    return logging.getLogger(f"llm_code.{name}")

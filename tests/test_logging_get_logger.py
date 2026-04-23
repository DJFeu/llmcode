"""Tests for ``llm_code.logging.get_logger`` — double-prefix regression.

v2.2.2 shipped with a subtle bug: callers pass ``__name__``
(already ``llm_code.<submodule>``) and ``get_logger`` unconditionally
prepended another ``llm_code.`` prefix, producing WARNING output like
``llm_code.llm_code.view.stream_renderer``. Fixed in v2.2.3.
"""
from __future__ import annotations

from llm_code.logging import get_logger


class TestGetLoggerNaming:
    def test_dunder_name_style_not_double_prefixed(self) -> None:
        """Passing ``__name__`` style dotted name must not be
        re-prefixed."""
        logger = get_logger("llm_code.view.stream_renderer")
        assert logger.name == "llm_code.view.stream_renderer"
        assert "llm_code.llm_code" not in logger.name

    def test_short_name_gets_prefixed(self) -> None:
        """Short names (no dotted package) still get the prefix so
        the namespace rule holds for third-party callers."""
        logger = get_logger("mymodule")
        assert logger.name == "llm_code.mymodule"

    def test_bare_llm_code_not_double_prefixed(self) -> None:
        logger = get_logger("llm_code")
        assert logger.name == "llm_code"

    def test_nested_submodule_preserved(self) -> None:
        logger = get_logger("llm_code.api.openai_compat")
        assert logger.name == "llm_code.api.openai_compat"

    def test_unrelated_dotted_name_gets_prefix(self) -> None:
        """A dotted name that doesn't start with ``llm_code.`` is
        still prefixed — prevents accidental cross-namespace
        leakage."""
        logger = get_logger("third_party.tool")
        assert logger.name == "llm_code.third_party.tool"

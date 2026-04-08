"""Round-2 Issue 1 + Issue 3 regression tests.

Issue 1: the XML tool-call fallback retry path in conversation.py used to
call `self._provider.stream_message(request)` *unguarded*, sitting inside
an outer `except Exception as exc:` handler with no try/finally. If that
retry call itself raised, the previously-opened `_llm_span` was orphaned.

Issue 3: `_truncate_for_attribute` was imported lazily inside a per-call
try/except. It must be hoisted to module-level imports so genuine import
bugs surface and there is no per-call overhead.

Both are verified via source inspection — driving the live conversation
runner requires fixtures that don't exist in this test layer.
"""
from __future__ import annotations

import ast
from pathlib import Path

import llm_code.runtime.conversation as conv_module


_SRC_PATH = Path(conv_module.__file__)
_SRC = _SRC_PATH.read_text()


def test_xml_fallback_stream_message_retry_is_guarded() -> None:
    """The retry `stream_message` call in the XML fallback branch must be
    wrapped so any exception triggers `_close_llm_span_with_error` before
    propagating — otherwise the open `_llm_span` is leaked.

    We grep for the second `stream_message(request)` occurrence (the one
    inside the recovery handler) and confirm that the surrounding 30 lines
    contain a try/except referencing `_close_llm_span_with_error`.
    """
    lines = _SRC.splitlines()
    sm_lines = [
        i for i, line in enumerate(lines)
        if "self._provider.stream_message(request)" in line
    ]
    # There should be at least 2 stream_message calls: the initial one and
    # the XML-fallback retry. Find the LAST one (which is the retry inside
    # the recovery branch).
    assert len(sm_lines) >= 2, (
        f"expected >=2 stream_message(request) call sites, found {len(sm_lines)}"
    )
    retry_line = sm_lines[-1]
    # Look for `_close_llm_span_with_error` within +/- 25 lines of the retry
    window = "\n".join(lines[max(0, retry_line - 5): retry_line + 25])
    assert "_close_llm_span_with_error" in window, (
        "XML fallback retry stream_message is not guarded by "
        "_close_llm_span_with_error — span will leak on retry failure.\n"
        f"window:\n{window}"
    )
    assert ("try:" in window) or ("try :" in window), (
        "Retry stream_message must be wrapped in try/except for span cleanup"
    )


def test_truncate_for_attribute_imported_at_module_top() -> None:
    """Issue 3: import must be hoisted to module-level (not inside a
    function body try/except)."""
    tree = ast.parse(_SRC)
    top_level_imports: list[str] = []
    for node in tree.body:  # only direct children of module
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                top_level_imports.append(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.append(alias.name)
    assert "_truncate_for_attribute" in top_level_imports, (
        "_truncate_for_attribute must be a top-level import in conversation.py"
    )


def test_no_inline_truncate_for_attribute_import_in_function_bodies() -> None:
    """The lazy `from llm_code.runtime.telemetry import _truncate_for_attribute`
    inside the post-stream enrichment block must be removed."""
    # Walk all function bodies and assert no nested ImportFrom for this name.
    tree = ast.parse(_SRC)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "llm_code.runtime.telemetry":
            for alias in node.names:
                if alias.name == "_truncate_for_attribute":
                    # check that this import is at module level (parent is Module)
                    # We do this by line number: top-level imports were already
                    # collected; any other occurrence is an offender.
                    offenders.append(node.lineno)
    # The top-level import is one occurrence; anything beyond that is bad.
    assert len(offenders) <= 1, (
        f"_truncate_for_attribute is imported multiple times "
        f"(should be hoisted to module top only). Lines: {offenders}"
    )

"""Tests for :mod:`llm_code.migrate.v12.rewriters.prompt_mode_import`."""
from __future__ import annotations

import libcst as cst

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters.prompt_mode_import import (
    PromptModeImportRewriter,
)


def _rewrite(source: str) -> tuple[str, Diagnostics]:
    diag = Diagnostics()
    rewriter = PromptModeImportRewriter(diag)
    rewriter.set_path("foo.py")
    module = cst.parse_module(source).visit(rewriter)
    return module.code, diag


class TestSingleImport:
    def test_rewrites_simple_from_import(self) -> None:
        src = "from llm_code.runtime.prompts.mode import beast\n"
        new, diag = _rewrite(src)
        assert "from llm_code.engine.prompt_builder import PromptBuilder" in new
        assert 'beast = PromptBuilder(template_path="modes/beast.j2")' in new
        assert "llm_code.runtime.prompts.mode" not in new
        assert not diag.any()

    def test_rewrites_aliased_from_import(self) -> None:
        src = "from llm_code.runtime.prompts.mode import beast as B\n"
        new, _ = _rewrite(src)
        assert 'B = PromptBuilder(template_path="modes/beast.j2")' in new
        assert "llm_code.runtime.prompts.mode" not in new

    def test_multiple_names_from_same_module(self) -> None:
        src = "from llm_code.runtime.prompts.mode import beast, plan\n"
        new, _ = _rewrite(src)
        assert 'beast = PromptBuilder(template_path="modes/beast.j2")' in new
        assert 'plan = PromptBuilder(template_path="modes/plan.j2")' in new

    def test_preserves_future_import(self) -> None:
        src = (
            "from __future__ import annotations\n"
            "from llm_code.runtime.prompts.mode import beast\n"
        )
        new, _ = _rewrite(src)
        # The future import must remain first.
        first_line = new.splitlines()[0]
        assert first_line == "from __future__ import annotations"
        assert "PromptBuilder" in new

    def test_preserves_docstring(self) -> None:
        src = (
            '"""Module docstring."""\n'
            "from llm_code.runtime.prompts.mode import beast\n"
        )
        new, _ = _rewrite(src)
        # Docstring stays at the top.
        assert new.startswith('"""Module docstring."""')


class TestUnsupported:
    def test_bare_import_emits_diagnostic(self) -> None:
        src = "import llm_code.runtime.prompts.mode.beast\n"
        new, diag = _rewrite(src)
        # Unsupported shape — source unchanged plus diagnostic emitted.
        assert new == src
        patterns = {e.pattern for e in diag.entries}
        assert "bare_import_prompt_mode" in patterns

    def test_unrelated_mode_module_not_touched(self) -> None:
        src = "from some.other.mode import thing\n"
        new, _ = _rewrite(src)
        assert new == src


class TestIdempotence:
    def test_second_run_is_noop(self) -> None:
        src = "from llm_code.runtime.prompts.mode import beast\n"
        first, _ = _rewrite(src)
        second, diag = _rewrite(first)
        assert first == second
        assert not diag.any()


class TestMultipleModules:
    def test_aliased_and_unaliased_mixed(self) -> None:
        src = (
            "from llm_code.runtime.prompts.mode import beast, plan as P\n"
        )
        new, _ = _rewrite(src)
        assert 'beast = PromptBuilder(template_path="modes/beast.j2")' in new
        assert 'P = PromptBuilder(template_path="modes/plan.j2")' in new


class TestCallSitePreserved:
    def test_body_is_untouched(self) -> None:
        src = (
            "from llm_code.runtime.prompts.mode import beast\n\n"
            "def foo():\n"
            "    return beast.format(x=1)\n"
        )
        new, _ = _rewrite(src)
        # This rewriter does NOT rewrite the `.format` call — that's
        # :mod:`prompt_format_call`'s job. Verify we left it alone.
        assert "return beast.format(x=1)" in new

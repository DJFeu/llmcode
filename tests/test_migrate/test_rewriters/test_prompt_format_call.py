"""Tests for :mod:`llm_code.migrate.v12.rewriters.prompt_format_call`."""
from __future__ import annotations

import libcst as cst

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters.prompt_format_call import (
    PromptFormatCallRewriter,
)


def _rewrite(source: str) -> tuple[str, Diagnostics]:
    diag = Diagnostics()
    rewriter = PromptFormatCallRewriter(diag)
    rewriter.set_path("foo.py")
    module = cst.parse_module(source).visit(rewriter)
    return module.code, diag


class TestBuilderName:
    def test_name_bound_to_builder_gets_run_index(self) -> None:
        src = (
            'from llm_code.engine.prompt_builder import PromptBuilder\n'
            'beast = PromptBuilder(template_path="modes/beast.j2")\n\n'
            'def foo(task):\n'
            '    return beast.format(task=task)\n'
        )
        new, diag = _rewrite(src)
        assert 'beast.run(task=task)["prompt"]' in new
        assert "PromptBuilder(template=" not in new
        assert not diag.any()

    def test_two_kwargs_preserved(self) -> None:
        src = (
            'from llm_code.engine.prompt_builder import PromptBuilder\n'
            'tpl = PromptBuilder(template_path="modes/x.j2")\n\n'
            'x = tpl.format(a=1, b=2)\n'
        )
        new, _ = _rewrite(src)
        assert 'tpl.run(a=1, b=2)["prompt"]' in new


class TestWrappedName:
    def test_legacy_prompt_import_is_wrapped(self) -> None:
        # Only receivers traceable to the legacy prompt tree get
        # rewritten — see ``_collect_prompt_candidates``. A bare
        # parameter name without a legacy import is NOT rewritten
        # (prevents clobbering i18n tables / SQL templates that
        # happen to share the ``.format(**kwargs)`` shape).
        src = (
            'from llm_code.runtime.prompts.mode import plan_prompt\n\n'
            'def foo(task):\n'
            '    return plan_prompt.format(task=task)\n'
        )
        new, diag = _rewrite(src)
        assert (
            'PromptBuilder(template=plan_prompt).run(task=task)["prompt"]'
            in new
        )
        # Rewriter needs to pull PromptBuilder into scope.
        assert "from llm_code.engine.prompt_builder import PromptBuilder" in new
        assert not diag.any()

    def test_preserves_star_kwargs(self) -> None:
        src = (
            'from llm_code.runtime.prompts import beast_prompt\n\n'
            'def foo(kw):\n'
            '    return beast_prompt.format(**kw)\n'
        )
        new, diag = _rewrite(src)
        assert (
            'PromptBuilder(template=beast_prompt).run(**kw)["prompt"]' in new
        )
        assert not diag.any()

    def test_unimported_name_is_not_rewritten(self) -> None:
        # ``prompt`` is a local parameter, NOT traceable to the legacy
        # prompt tree. Leaving it alone is the safe default — the
        # plugin author can rename via ``docs/plugin_migration_guide.md``
        # if they actually meant a prompt.
        src = (
            'def foo(prompt, task):\n'
            '    return prompt.format(task=task)\n'
        )
        new, diag = _rewrite(src)
        assert new == src
        assert not diag.any()


class TestUnsupported:
    def test_positional_args_flagged(self) -> None:
        src = (
            'def foo(prompt, task):\n'
            '    return prompt.format(task)\n'
        )
        new, diag = _rewrite(src)
        assert new == src
        patterns = {e.pattern for e in diag.entries}
        assert "positional_format_args" in patterns

    def test_string_literal_format_not_touched(self) -> None:
        # "foo {}".format(x) has a non-Name receiver -> skip.
        src = 'x = "foo {}".format(y)\n'
        new, _ = _rewrite(src)
        assert new == src

    def test_unrelated_format_call_untouched(self) -> None:
        # ``f`` has no legacy prompt origin — leave i18n / SQL-template
        # style ``.format()`` call sites untouched so the codemod can
        # run against real plugin source without miscompiling.
        src = 'x = f.format(y=1)\n'
        new, diag = _rewrite(src)
        assert new == src
        assert not diag.any()


class TestIdempotence:
    def test_second_run_is_noop_on_builder_path(self) -> None:
        src = (
            'from llm_code.engine.prompt_builder import PromptBuilder\n'
            'beast = PromptBuilder(template_path="modes/beast.j2")\n\n'
            'x = beast.format(task=1)\n'
        )
        first, _ = _rewrite(src)
        second, diag = _rewrite(first)
        assert first == second
        assert not diag.any()

    def test_second_run_is_noop_on_wrap_path(self) -> None:
        src = (
            'def foo(p):\n'
            '    return p.format(a=1)\n'
        )
        first, _ = _rewrite(src)
        second, diag = _rewrite(first)
        assert first == second
        assert not diag.any()


class TestImportIdempotence:
    def test_does_not_double_import_prompt_builder(self) -> None:
        src = (
            'from llm_code.engine.prompt_builder import PromptBuilder\n'
            '\n'
            'def foo(p):\n'
            '    return p.format(a=1)\n'
        )
        new, _ = _rewrite(src)
        # Exactly one import — no duplicate line inserted.
        assert new.count("from llm_code.engine.prompt_builder") == 1

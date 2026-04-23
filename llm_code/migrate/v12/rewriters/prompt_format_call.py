"""Rewriter — ``prompt.format(**kw)`` -> PromptBuilder equivalent.

Rewrites call sites of the form::

    prompt.format(task=task, files=files)

into::

    PromptBuilder(template=prompt).run(task=task, files=files)["prompt"]

Scope rules:

- Only rewrites ``.format(...)`` calls that appear to operate on a
  *prompt string* variable. The heuristic: the receiver must be a
  plain :class:`libcst.Name`. We additionally avoid rewriting calls on
  names that look like they come from unrelated sources (e.g.
  ``"foo {}".format(...)`` on a literal — different shape of receiver).
- If :mod:`prompt_mode_import` has already reassigned the variable to a
  :class:`PromptBuilder` instance in the same module, the heuristic
  above still fires (the name is still a bare ``Name``) but the
  rewrite would be incorrect. To handle that, the rewriter inspects
  module-level assignments first and *excludes* any name that is
  already bound to ``PromptBuilder(...)``; those receive a different
  rewrite (direct ``.run(...)["prompt"]``).
- Positional-only args are unsupported — the original source is left
  alone and a diagnostic is emitted so the user converts to kwargs.

Compatible with :mod:`prompt_mode_import` as follows — when that
rewriter has run first, the module already contains lines like::

    beast = PromptBuilder(template_path="modes/beast.j2")

For those names the downstream format call::

    beast.format(task=task)

is rewritten to::

    beast.run(task=task)["prompt"]

For names that are *not* bound to a ``PromptBuilder`` instance at
module scope, we wrap the receiver::

    some_inline_prompt.format(task=task)

becomes::

    PromptBuilder(template=some_inline_prompt).run(task=task)["prompt"]
"""
from __future__ import annotations

from typing import Any

import libcst as cst
from libcst import matchers as m

from llm_code.migrate.v12.diagnostics import Diagnostics


class PromptFormatCallRewriter(cst.CSTTransformer):
    """Rewrite ``<name>.format(**kwargs)`` call sites.

    See module docstring for the dispatch rules.
    """

    def __init__(self, diagnostics: Diagnostics) -> None:
        super().__init__()
        self._diag = diagnostics
        self._current_path: str = "<unknown>"
        # Names bound to ``PromptBuilder(...)`` at module scope — those
        # are rewritten as ``name.run(...)["prompt"]`` instead of being
        # wrapped.
        self._builder_names: set[str] = set()
        # Names imported from the legacy ``llm_code.runtime.prompts[.*]``
        # tree. Only ``<name>.format(...)`` call sites whose receiver
        # maps to one of these are rewritten — a naive blanket rewrite
        # would clobber every call-site with a ``.format(**kwargs)``
        # (i18n tables, SQL template builders, etc.) that happens to
        # live in the plugin source.
        self._prompt_candidates: set[str] = set()
        self._needs_builder_import = False
        self._builder_imported = False

    def set_path(self, path: str) -> None:
        self._current_path = path

    def visit_Module(self, node: cst.Module) -> None:
        self._builder_names = _collect_builder_names(node)
        self._prompt_candidates = _collect_prompt_candidates(node)
        self._needs_builder_import = False
        self._builder_imported = _already_imports_prompt_builder(node)

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> Any:
        # Only match ``<Name>.format(...)``.
        if not m.matches(
            updated_node,
            m.Call(func=m.Attribute(value=m.Name(), attr=m.Name("format"))),
        ):
            return updated_node

        func = updated_node.func
        assert isinstance(func, cst.Attribute)
        receiver = func.value
        assert isinstance(receiver, cst.Name)
        name = receiver.value

        # Reject positional-only args; rewriting positional to keyword
        # requires knowing the format string which is not guaranteed
        # to be statically available.
        for arg in updated_node.args:
            if arg.keyword is None and arg.star == "":
                self._diag.report(
                    pattern="positional_format_args",
                    path=self._current_path,
                    line=0,
                    rewriter="prompt_format_call",
                    suggestion=(
                        "Convert positional `.format(a, b)` to keyword "
                        "args so the codemod can rewrite it."
                    ),
                )
                return updated_node

        args = list(updated_node.args)

        if name in self._builder_names:
            # name is already a PromptBuilder -> just swap `.format` for
            # `.run` and index into ``["prompt"]``.
            return _indexed_run(receiver, args)

        if name not in self._prompt_candidates:
            # Receiver isn't traceable to a legacy prompt import and
            # isn't a known PromptBuilder — leave the call alone.
            # Stops the rewriter from mangling generic ``.format()``
            # call sites like i18n catalogs or SQL templates.
            return updated_node

        # Otherwise wrap: ``PromptBuilder(template=<name>).run(...)["prompt"]``
        self._needs_builder_import = True
        builder_call = cst.Call(
            func=cst.Name("PromptBuilder"),
            args=[
                cst.Arg(
                    keyword=cst.Name("template"),
                    value=receiver,
                    equal=cst.AssignEqual(
                        whitespace_before=cst.SimpleWhitespace(""),
                        whitespace_after=cst.SimpleWhitespace(""),
                    ),
                )
            ],
        )
        return _indexed_run(builder_call, args)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if (
            not self._needs_builder_import
            or self._builder_imported
            or _already_imports_prompt_builder(updated_node)
        ):
            return updated_node
        new_body = list(updated_node.body)
        insertion = _insertion_point(new_body)
        new_body.insert(
            insertion,
            cst.parse_statement(
                "from llm_code.engine.prompt_builder import PromptBuilder"
            ),
        )
        return updated_node.with_changes(body=new_body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_builder_names(module: cst.Module) -> set[str]:
    """Find module-level ``NAME = PromptBuilder(...)`` assignments."""
    names: set[str] = set()
    builder_call = m.Call(func=m.Name("PromptBuilder"))
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.Assign):
                continue
            if not m.matches(sub.value, builder_call):
                continue
            for target in sub.targets:
                if isinstance(target.target, cst.Name):
                    names.add(target.target.value)
    return names


def _collect_prompt_candidates(module: cst.Module) -> set[str]:
    """Find local names imported from the legacy prompt tree.

    Matches:

    - ``from llm_code.runtime.prompts import NAME``
    - ``from llm_code.runtime.prompts.mode import NAME``
    - ``from llm_code.runtime.prompts.modes import NAME``
    - ``from llm_code.runtime.prompts.models import NAME``
    - nested aliases (``... import original as local``)

    Function-scoped imports are NOT walked — rewriting a call site whose
    receiver resolves through a function-local import is unsafe without
    full scope analysis, and the codemod would rather emit an
    unsupported-pattern diagnostic than silently miscompile.
    """
    candidates: set[str] = set()
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.ImportFrom):
                continue
            mod = _dotted_name(sub.module) if sub.module else ""
            if not (
                mod == "llm_code.runtime.prompts"
                or mod.startswith("llm_code.runtime.prompts.")
            ):
                continue
            names = sub.names
            if not hasattr(names, "__iter__"):
                continue
            for alias in names:
                if not isinstance(alias, cst.ImportAlias):
                    continue
                if alias.asname is not None and isinstance(
                    alias.asname.name, cst.Name
                ):
                    candidates.add(alias.asname.name.value)
                else:
                    candidates.add(_dotted_name(alias.name))
    return candidates


def _already_imports_prompt_builder(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.ImportFrom):
                continue
            if _dotted_name(sub.module) == "llm_code.engine.prompt_builder":
                return True
    return False


def _indexed_run(
    receiver: cst.BaseExpression, args: list[cst.Arg]
) -> cst.Subscript:
    """Build ``<receiver>.run(<args>)["prompt"]``."""
    call = cst.Call(
        func=cst.Attribute(value=receiver, attr=cst.Name("run")),
        args=args,
    )
    return cst.Subscript(
        value=call,
        slice=[
            cst.SubscriptElement(
                slice=cst.Index(value=cst.SimpleString('"prompt"'))
            )
        ],
    )


def _dotted_name(node: cst.CSTNode | None) -> str:
    if node is None:
        return ""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr.value}"
    return ""


def _insertion_point(body: list[cst.BaseStatement]) -> int:
    idx = 0
    if body and _is_docstring(body[0]):
        idx = 1
    while idx < len(body) and _is_future_import(body[idx]):
        idx += 1
    return idx


def _is_docstring(stmt: cst.BaseStatement) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    if not stmt.body:
        return False
    first = stmt.body[0]
    return isinstance(first, cst.Expr) and isinstance(
        first.value, cst.SimpleString
    )


def _is_future_import(stmt: cst.BaseStatement) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    for sub in stmt.body:
        if (
            isinstance(sub, cst.ImportFrom)
            and isinstance(sub.module, cst.Name)
            and sub.module.value == "__future__"
        ):
            return True
    return False

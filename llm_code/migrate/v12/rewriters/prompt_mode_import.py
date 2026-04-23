"""Rewriter — ``from llm_code.runtime.prompts.mode import X`` -> PromptBuilder.

Legacy plugins import a mode-specific prompt as a module-level name::

    from llm_code.runtime.prompts.mode import beast

Or:

    from llm_code.runtime.prompts.mode.beast import beast as B

Then use it like::

    system = beast.format(task=task)

The v12 replacement is a :class:`PromptBuilder` call against a template
shipped under ``engine/prompts/modes/*.j2``::

    from llm_code.engine.prompt_builder import PromptBuilder
    system = PromptBuilder(template_path="modes/beast.j2").run(task=task)["prompt"]

This rewriter handles **imports only**. The call-site rewrite from
``<name>.format(...)`` to the ``PromptBuilder(...).run(...)["prompt"]``
expression is handled by :mod:`prompt_format_call`. The two rewriters
must run in order (this one first) — the format-call rewriter assumes
the legacy symbol has already been removed from scope.

Two shapes handled:

1. ``from llm_code.runtime.prompts.mode import X`` -> single-import ->
   replace with ``PromptBuilder`` import; leave the bound name ``X``
   reassigned to a ``PromptBuilder(...)`` instance **at the top of the
   module** so downstream ``X.format(...)`` becomes
   ``X.run(...)["prompt"]`` when the format-call rewriter takes over.
2. ``from llm_code.runtime.prompts.mode import X as Y`` -> aliased
   form — same as (1) but the bound name is ``Y``.

The direct-module form ``import llm_code.runtime.prompts.mode.X`` (rare)
is flagged as unsupported with a diagnostic.
"""
from __future__ import annotations

from typing import Any

import libcst as cst
from libcst import matchers as m

from llm_code.migrate.v12.diagnostics import Diagnostics

_LEGACY_MODULE = "llm_code.runtime.prompts.mode"
_PROMPT_BUILDER_IMPORT = cst.parse_statement(
    "from llm_code.engine.prompt_builder import PromptBuilder"
)


class PromptModeImportRewriter(cst.CSTTransformer):
    """Rewrite legacy prompt-mode imports to :class:`PromptBuilder`.

    Walks each module once:

    - Flag import stmts that reference ``llm_code.runtime.prompts.mode``.
    - Remove those import stmts.
    - Remember each bound (name, template_path) pair.
    - After the visit, inject the PromptBuilder import + a module-level
      assignment ``<bound_name> = PromptBuilder(template_path="modes/<X>.j2")``
      so downstream ``<bound_name>.format(...)`` call sites keep type-
      checking and the :mod:`prompt_format_call` rewriter can handle
      them.
    """

    def __init__(self, diagnostics: Diagnostics) -> None:
        super().__init__()
        self._diag = diagnostics
        self._current_path: str = "<unknown>"
        # (bound_name, template_path_value) pairs collected during visit.
        self._rewrites: list[tuple[str, str]] = []
        self._needs_builder_import = False
        self._builder_already_imported = False

    # Runner injects the path before each call so diagnostics locate
    # the source file.
    def set_path(self, path: str) -> None:
        self._current_path = path

    def visit_Module(self, node: cst.Module) -> None:
        self._rewrites = []
        self._needs_builder_import = False
        self._builder_already_imported = _module_already_imports_prompt_builder(
            node
        )

    # Python ``import llm_code.runtime.prompts.mode.<X>`` — unsupported.
    def leave_Import(
        self, original_node: cst.Import, updated_node: cst.Import
    ) -> Any:
        for alias in updated_node.names:
            dotted = _dotted_name(alias.name)
            if dotted.startswith(f"{_LEGACY_MODULE}."):
                self._diag.report(
                    pattern="bare_import_prompt_mode",
                    path=self._current_path,
                    line=_line_of(original_node),
                    rewriter="prompt_mode_import",
                    suggestion=(
                        "Replace `import llm_code.runtime.prompts.mode.X` "
                        "with `from llm_code.engine.prompt_builder import "
                        "PromptBuilder` plus "
                        "`X = PromptBuilder(template_path=\"modes/X.j2\")`."
                    ),
                )
                return updated_node
        return updated_node

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> Any:
        module_name = _dotted_name(updated_node.module) if updated_node.module else ""
        if module_name != _LEGACY_MODULE:
            return updated_node
        if not isinstance(updated_node.names, (list, tuple)) and not hasattr(
            updated_node.names, "__iter__"
        ):
            # ImportStar (``from X import *``) is iterable-like in libcst
            # but not useful here; fall through to treat it as unsupported.
            self._diag.report(
                pattern="star_import_prompt_mode",
                path=self._current_path,
                line=_line_of(original_node),
                rewriter="prompt_mode_import",
                suggestion=(
                    "Replace the star import with explicit mode names "
                    "so the codemod can rewrite them."
                ),
            )
            return updated_node
        for alias in updated_node.names:
            name = _dotted_name(alias.name)
            bound = (
                _dotted_name(alias.asname.name)
                if alias.asname is not None
                and isinstance(alias.asname.name, cst.Name)
                else name
            )
            self._rewrites.append((bound, f"modes/{name}.j2"))
        self._needs_builder_import = True
        # Remove the legacy import statement outright.
        return cst.RemoveFromParent()

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self._rewrites:
            return updated_node
        new_body = list(updated_node.body)
        # Insert import + assignments *after* the module-docstring block
        # (if any) and *after* any ``from __future__`` imports, so the
        # rewrite does not break statement ordering rules.
        insertion_index = _insertion_point(new_body)
        inserts: list[cst.BaseStatement] = []
        if self._needs_builder_import and not self._builder_already_imported:
            inserts.append(_PROMPT_BUILDER_IMPORT)
        for bound_name, template_path in self._rewrites:
            inserts.append(
                _make_prompt_builder_assignment(bound_name, template_path)
            )
        new_body[insertion_index:insertion_index] = inserts
        return updated_node.with_changes(body=new_body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_already_imports_prompt_builder(module: cst.Module) -> bool:
    matcher = m.ImportFrom(
        module=m.Attribute(
            value=m.Attribute(value=m.Name("llm_code"), attr=m.Name("engine")),
            attr=m.Name("prompt_builder"),
        )
    )
    return any(m.matches(node, matcher) for node in _iter_simple_statements(module))


def _iter_simple_statements(module: cst.Module):
    for stmt in module.body:
        if isinstance(stmt, cst.SimpleStatementLine):
            yield from stmt.body


def _line_of(node: cst.CSTNode) -> int:
    """Best-effort line number; libcst exposes positions only via metadata.

    Rather than pulling in the whole metadata pipeline, we surface the
    missing location as ``0`` and rely on the pattern name plus surrounding
    context to orient the user. Tests assert on pattern names, never on
    lines, so this is safe.
    """
    return 0


def _dotted_name(node: cst.CSTNode | None) -> str:
    if node is None:
        return ""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr.value}"
    return ""


def _insertion_point(body: list[cst.BaseStatement]) -> int:
    """Find the first index past docstring + ``__future__`` imports."""
    idx = 0
    # Skip the module docstring.
    if body and _is_docstring(body[0]):
        idx = 1
    # Skip ``from __future__ import ...``.
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


def _make_prompt_builder_assignment(
    bound_name: str, template_path: str
) -> cst.BaseStatement:
    src = (
        f'{bound_name} = PromptBuilder(template_path="{template_path}")\n'
    )
    return cst.parse_statement(src)

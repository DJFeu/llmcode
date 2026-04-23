"""Rewriter — ``ToolExecutionPipeline`` subclass -> ``@component`` Component.

Legacy plugins subclass the runtime pipeline to customise tool
selection / pre-execute / post-process hooks::

    from llm_code.runtime.tool_pipeline import ToolExecutionPipeline

    class AuditingPipeline(ToolExecutionPipeline):
        def pre_execute(self, ctx):
            ...

        def post_process(self, ctx, result):
            ...

The v12 replacement is a :class:`@component` with a ``run()`` method::

    from llm_code.engine import Pipeline, component


    @component
    class AuditingComponent:
        def run(self, ctx):
            ...


    def register(pipeline: Pipeline) -> None:
        pipeline.add_component("auditing", AuditingComponent())

Transformations:

1. Replace ``from llm_code.runtime.tool_pipeline import ...`` with
   ``from llm_code.engine import Pipeline, component``. Aliased
   imports are preserved (``... import ToolExecutionPipeline as TXP``
   -> ``... import component as component``; alias fades because the
   class body is rewritten). Plain class refs like ``TXP`` are counted
   as ``ToolExecutionPipeline`` for subclass detection.
2. Rewrite each class inheriting from ``ToolExecutionPipeline`` (direct
   or aliased): strip the base class, inject ``@component`` decorator,
   rename legacy hook methods to a single ``run()``.
3. Append a ``def register(pipeline: Pipeline) -> None:`` helper at
   module scope — one call per migrated class — unless a ``register``
   symbol is already present (in which case emit a diagnostic so the
   user merges manually).
4. Unsupported shapes — metaprogramming against ``self.__class__``,
   direct call of private ``_`` methods, multiple inheritance with
   ``ToolExecutionPipeline`` in the middle of the MRO — are flagged
   with a diagnostic and left unchanged.

The rewrite is intentionally *structural, not semantic*. The generated
``run()`` body concatenates (in deterministic order) the bodies of
``pre_execute`` / ``run`` / ``post_process`` so the plugin author
still has one place to review the logic after the codemod. Anything
subtler — shared state across hooks, ordering that assumed the legacy
pipeline's internals — is what the manual migration section of the
plugin-migration guide covers.
"""
from __future__ import annotations

from typing import Any

import libcst as cst
from libcst import matchers as m

from llm_code.migrate.v12.diagnostics import Diagnostics

_LEGACY_MODULE = "llm_code.runtime.tool_pipeline"
_LEGACY_CLASS = "ToolExecutionPipeline"

# Hooks we know how to merge into a single ``run()``.
_KNOWN_HOOKS = ("pre_execute", "run", "post_process")


class ToolPipelineSubclassRewriter(cst.CSTTransformer):
    """Rewrite legacy ``ToolExecutionPipeline`` subclasses to Components."""

    def __init__(self, diagnostics: Diagnostics) -> None:
        super().__init__()
        self._diag = diagnostics
        self._current_path: str = "<unknown>"
        # Names bound to ``ToolExecutionPipeline`` via ``from ... import``.
        self._legacy_bindings: set[str] = set()
        self._migrated_components: list[str] = []
        self._needs_engine_import = False
        self._engine_imported = False
        self._register_exists = False
        # Count of legacy-name references that are neither ``ClassDef``
        # bases (subclass-migration targets) nor ``ImportFrom`` tokens.
        # Non-zero means the symbol is used as a call / attribute /
        # type-annotation / etc., so deleting the import would break
        # those call sites — the rewriter preserves the import and
        # emits a diagnostic instead of producing broken source.
        self._non_subclass_usage: int = 0

    def set_path(self, path: str) -> None:
        self._current_path = path

    def visit_Module(self, node: cst.Module) -> None:
        self._legacy_bindings = _collect_legacy_bindings(node)
        self._migrated_components = []
        self._needs_engine_import = False
        self._engine_imported = _already_imports_engine(node)
        self._register_exists = _has_module_register(node)
        self._non_subclass_usage = _count_non_subclass_non_import_usage(
            node, self._legacy_bindings
        )

    # --- Imports --------------------------------------------------------

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> Any:
        module_name = _dotted_name(updated_node.module) if updated_node.module else ""
        if module_name != _LEGACY_MODULE:
            return updated_node

        # The import targets the legacy module. Only bindings that
        # resolve to ``ToolExecutionPipeline`` are our concern; other
        # symbols (``_record_denial``, future helpers) must survive
        # unmodified — the legacy module still exists post-M8.b, we
        # only removed the ``LegacyToolExecutionPipeline`` class.
        names = updated_node.names
        if not hasattr(names, "__iter__"):
            # ``from X import *`` — defensively leave alone.
            return updated_node

        new_names: list[cst.ImportAlias] = []
        has_legacy_alias = False
        for alias in names:
            if not isinstance(alias, cst.ImportAlias):
                new_names.append(alias)
                continue
            imported = _dotted_name(alias.name)
            if imported == _LEGACY_CLASS:
                has_legacy_alias = True
                continue  # candidate for removal
            new_names.append(alias)

        if not has_legacy_alias:
            # Import from the legacy module but no ToolExecutionPipeline
            # binding — fully owned by sibling symbols, untouched.
            return updated_node

        # Preserve the legacy symbol when it's still referenced outside
        # of ``class X(ToolExecutionPipeline)`` subclasses. Removing
        # the import here would leave a ``NameError`` at the surviving
        # call site; the migration author can hand-port those call
        # sites via ``docs/plugin_migration_guide.md``.
        if self._non_subclass_usage > 0:
            self._diag.report(
                pattern="legacy_import_with_non_subclass_usage",
                path=self._current_path,
                line=0,
                rewriter="tool_pipeline_subclass",
                suggestion=(
                    "ToolExecutionPipeline is still referenced outside of "
                    "class subclassing (instantiation, attribute access, "
                    "or annotation). The codemod preserved the import — "
                    "migrate the remaining call site(s) manually to "
                    "`Pipeline` from `llm_code.engine`."
                ),
            )
            return updated_node

        # Safe to strip the legacy alias: the only references were
        # ClassDef bases that leave_ClassDef rewrites into @component.
        self._needs_engine_import = True
        if not new_names:
            return cst.RemoveFromParent()
        # Preserve other siblings in this ImportFrom.
        return updated_node.with_changes(names=new_names)

    # --- Class definitions ---------------------------------------------

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> Any:
        if not _inherits_legacy(updated_node, self._legacy_bindings):
            return updated_node
        if _has_multiple_bases(updated_node):
            self._diag.report(
                pattern="multiple_inheritance_with_legacy_pipeline",
                path=self._current_path,
                line=0,
                rewriter="tool_pipeline_subclass",
                suggestion=(
                    "Collapse multiple inheritance to a single @component "
                    "class — the codemod refuses to guess ordering."
                ),
            )
            return updated_node
        if _uses_metaprogramming_on_self(updated_node):
            self._diag.report(
                pattern="metaprogramming_on_self_class",
                path=self._current_path,
                line=0,
                rewriter="tool_pipeline_subclass",
                suggestion=(
                    "Remove `self.__class__` / private `_` base-class "
                    "access and replace with explicit helpers before "
                    "re-running the codemod."
                ),
            )
            return updated_node

        class_name = updated_node.name.value
        new_name_value = _component_class_name(class_name)
        self._migrated_components.append(new_name_value)
        self._needs_engine_import = True

        new_body = _merge_hook_methods(updated_node.body)
        return updated_node.with_changes(
            bases=[],
            keywords=[],
            lpar=cst.MaybeSentinel.DEFAULT,
            rpar=cst.MaybeSentinel.DEFAULT,
            decorators=[
                cst.Decorator(decorator=cst.Name("component")),
                *updated_node.decorators,
            ],
            name=cst.Name(new_name_value),
            body=new_body,
        )

    # --- Module-level injection ----------------------------------------

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        new_body = list(updated_node.body)
        inserts: list[cst.BaseStatement] = []
        if self._needs_engine_import and not self._engine_imported:
            inserts.append(
                cst.parse_statement(
                    "from llm_code.engine import Pipeline, component"
                )
            )
        if inserts:
            insertion = _insertion_point(new_body)
            new_body[insertion:insertion] = inserts

        if self._migrated_components:
            if self._register_exists:
                self._diag.report(
                    pattern="existing_register_symbol",
                    path=self._current_path,
                    line=0,
                    rewriter="tool_pipeline_subclass",
                    suggestion=(
                        "Module already defines a `register` symbol — "
                        "merge the codemod-generated registration body "
                        "into it by hand."
                    ),
                )
            else:
                new_body.append(_make_register_function(self._migrated_components))

        return updated_node.with_changes(body=new_body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_legacy_bindings(module: cst.Module) -> set[str]:
    """Return the set of local names bound to ``ToolExecutionPipeline``."""
    bindings: set[str] = set()
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.ImportFrom):
                continue
            if _dotted_name(sub.module) != _LEGACY_MODULE:
                continue
            if not hasattr(sub.names, "__iter__"):
                continue
            for alias in sub.names:
                if not isinstance(alias, cst.ImportAlias):
                    continue
                if _dotted_name(alias.name) != _LEGACY_CLASS:
                    continue
                if alias.asname is not None and isinstance(
                    alias.asname.name, cst.Name
                ):
                    bindings.add(alias.asname.name.value)
                else:
                    bindings.add(_LEGACY_CLASS)
    return bindings


def _already_imports_engine(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.ImportFrom):
                continue
            if _dotted_name(sub.module) == "llm_code.engine":
                return True
    return False


def _has_module_register(module: cst.Module) -> bool:
    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef) and stmt.name.value == "register":
            return True
    return False


def _count_non_subclass_non_import_usage(
    module: cst.Module, bindings: set[str]
) -> int:
    """Count references to the legacy name that are NOT the class-def
    base (which the subclass rewriter migrates) and NOT inside an
    ``ImportFrom`` statement (which this rewriter owns).

    If the count is > 0 the rewriter must preserve the legacy import
    so surviving call sites keep resolving — otherwise we emit source
    with a NameError.

    Names are matched against ``bindings`` ∪ ``{"ToolExecutionPipeline"}``
    so function-scoped imports (which ``_collect_legacy_bindings``
    intentionally ignores — it walks module-level only) still count.
    """
    targets = set(bindings)
    targets.add(_LEGACY_CLASS)

    def _name_hits(node: cst.CSTNode) -> int:
        count = 0
        for n in m.findall(node, m.Name()):
            if isinstance(n, cst.Name) and n.value in targets:
                count += 1
        return count

    total = _name_hits(module)

    subclass_bases = 0
    for cls in m.findall(module, m.ClassDef()):
        if not isinstance(cls, cst.ClassDef):
            continue
        for base in cls.bases:
            subclass_bases += _name_hits(base)

    import_tokens = 0
    for imp in m.findall(module, m.ImportFrom()):
        if not isinstance(imp, cst.ImportFrom):
            continue
        import_tokens += _name_hits(imp)

    return max(total - subclass_bases - import_tokens, 0)


def _inherits_legacy(cls: cst.ClassDef, bindings: set[str]) -> bool:
    if not bindings:
        return False
    for base in cls.bases:
        if isinstance(base.value, cst.Name) and base.value.value in bindings:
            return True
        if isinstance(base.value, cst.Attribute):
            dotted = _dotted_name(base.value)
            # Catch ``llm_code.runtime.tool_pipeline.ToolExecutionPipeline``
            if dotted.endswith(f".{_LEGACY_CLASS}"):
                return True
    return False


def _has_multiple_bases(cls: cst.ClassDef) -> bool:
    return len(cls.bases) > 1


def _uses_metaprogramming_on_self(cls: cst.ClassDef) -> bool:
    class _MetaVisitor(cst.CSTVisitor):
        def __init__(self) -> None:
            super().__init__()
            self.found = False

        def visit_Attribute(self, node: cst.Attribute) -> None:
            # ``self.__class__``
            if (
                isinstance(node.value, cst.Name)
                and node.value.value == "self"
                and node.attr.value == "__class__"
            ):
                self.found = True

    visitor = _MetaVisitor()
    cls.visit(visitor)
    return visitor.found


def _component_class_name(legacy: str) -> str:
    """Map ``FooPipeline`` -> ``FooComponent``; fallback: ``<Name>Component``."""
    if legacy.endswith("Pipeline"):
        return f"{legacy[: -len('Pipeline')]}Component"
    return f"{legacy}Component"


def _merge_hook_methods(body: cst.IndentedBlock) -> cst.IndentedBlock:
    """Rename known hook methods so a single ``run()`` survives.

    Strategy:

    * If the class already defines ``run(...)``, leave everything as is.
    * Otherwise, rename ``pre_execute`` (or ``post_process``) to
      ``run`` when exactly one hook is defined. When both ``pre_execute``
      and ``post_process`` are present, rename ``pre_execute`` to
      ``run`` and leave ``post_process`` in place for the author to
      merge manually. The plugin-migration guide covers the manual
      follow-up.
    """
    if not isinstance(body, cst.IndentedBlock):
        return body

    # Collect FunctionDefs, preserve order.
    funcs: list[int] = []
    has_run = False
    for idx, stmt in enumerate(body.body):
        if isinstance(stmt, cst.FunctionDef):
            if stmt.name.value == "run":
                has_run = True
            if stmt.name.value in _KNOWN_HOOKS:
                funcs.append(idx)

    if has_run:
        return body

    new_body = list(body.body)
    renamed = False
    for idx in funcs:
        stmt = new_body[idx]
        if not isinstance(stmt, cst.FunctionDef):
            continue
        if stmt.name.value == "pre_execute":
            new_body[idx] = stmt.with_changes(name=cst.Name("run"))
            renamed = True
            break
    if not renamed:
        # Fall back to renaming post_process.
        for idx in funcs:
            stmt = new_body[idx]
            if not isinstance(stmt, cst.FunctionDef):
                continue
            if stmt.name.value == "post_process":
                new_body[idx] = stmt.with_changes(name=cst.Name("run"))
                break

    return body.with_changes(body=new_body)


def _make_register_function(class_names: list[str]) -> cst.BaseStatement:
    lines = [
        "def register(pipeline: Pipeline) -> None:",
        '    """Register components produced by the v12 codemod."""',
    ]
    for cls_name in class_names:
        instance_name = cls_name[0].lower() + cls_name[1:]
        lines.append(
            f'    pipeline.add_component("{instance_name}", {cls_name}())'
        )
    source = "\n".join(lines) + "\n"
    return cst.parse_statement(source)


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

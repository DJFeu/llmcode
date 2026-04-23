"""Rewriter registry for the v12 codemod.

Each rewriter is a factory callable that returns a fresh
``(transformer, diagnostics)`` pair where ``transformer`` is a
:class:`libcst.CSTTransformer` subclass instance and ``diagnostics`` is
a :class:`llm_code.migrate.v12.diagnostics.Diagnostics` collector the
transformer mutates while it walks the tree.

The registry is a plain dict mapping the *short name* (stable across
releases — used by CLI ``--rewriters=<names>``) to the factory.

Factory shape::

    def make() -> tuple[libcst.CSTTransformer, Diagnostics]:
        diag = Diagnostics()
        return MyTransformer(diag), diag

Python file rewriters operate on a :class:`libcst.Module`. The
``pyproject_constraint`` entry is the lone exception: it handles a TOML
file and exposes a callable with signature ``(source: str, path: str,
diagnostics: Diagnostics) -> str``. The runner branches on file
extension before dispatching.
"""
from __future__ import annotations

from typing import Callable

import libcst as cst

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters.prompt_format_call import (
    PromptFormatCallRewriter,
)
from llm_code.migrate.v12.rewriters.prompt_mode_import import (
    PromptModeImportRewriter,
)
from llm_code.migrate.v12.rewriters.pyproject_constraint import (
    rewrite_pyproject_source,
)
from llm_code.migrate.v12.rewriters.tool_pipeline_subclass import (
    ToolPipelineSubclassRewriter,
)

PythonRewriterFactory = Callable[[], tuple[cst.CSTTransformer, Diagnostics]]
PyprojectRewriter = Callable[[str, str, Diagnostics], str]


def _make_tool_pipeline_subclass() -> tuple[cst.CSTTransformer, Diagnostics]:
    diag = Diagnostics()
    return ToolPipelineSubclassRewriter(diag), diag


def _make_prompt_mode_import() -> tuple[cst.CSTTransformer, Diagnostics]:
    diag = Diagnostics()
    return PromptModeImportRewriter(diag), diag


def _make_prompt_format_call() -> tuple[cst.CSTTransformer, Diagnostics]:
    diag = Diagnostics()
    return PromptFormatCallRewriter(diag), diag


PYTHON_REWRITERS: dict[str, PythonRewriterFactory] = {
    "tool_pipeline_subclass": _make_tool_pipeline_subclass,
    "prompt_mode_import": _make_prompt_mode_import,
    "prompt_format_call": _make_prompt_format_call,
}

PYPROJECT_REWRITERS: dict[str, PyprojectRewriter] = {
    "pyproject_constraint": rewrite_pyproject_source,
}

ALL_REWRITERS: tuple[str, ...] = tuple(PYTHON_REWRITERS) + tuple(
    PYPROJECT_REWRITERS
)


def describe_rewriters() -> list[tuple[str, str]]:
    """Return ``(name, one_line_description)`` pairs for CLI help text."""
    return [
        (
            "tool_pipeline_subclass",
            "ToolExecutionPipeline subclass -> @component Component",
        ),
        (
            "prompt_mode_import",
            "runtime.prompts.mode.* imports -> PromptBuilder(template_path=...)",
        ),
        (
            "prompt_format_call",
            "prompt.format(**kw) -> PromptBuilder(template=prompt).run(**kw)['prompt']",
        ),
        (
            "pyproject_constraint",
            "bump llmcode dep constraint to >=2.0,<3.0 across poetry/PEP 621/hatch",
        ),
    ]


__all__ = [
    "ALL_REWRITERS",
    "PYPROJECT_REWRITERS",
    "PYTHON_REWRITERS",
    "PyprojectRewriter",
    "PythonRewriterFactory",
    "describe_rewriters",
]

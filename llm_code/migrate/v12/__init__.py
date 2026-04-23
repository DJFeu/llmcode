"""llm_code.migrate.v12 — codemod for v12 plugin migration.

Rewrites third-party plugin source to the v12 Component + PromptBuilder
API. Loaded as a standalone click subcommand (see
:mod:`llm_code.migrate.cli`), not wired into the main ``llmcode`` CLI.

Architecture:

* :mod:`llm_code.migrate.v12.runner` — walks a plugin tree, drives the
  rewriters, emits a unified diff (dry-run) or writes in place.
* :mod:`llm_code.migrate.v12.rewriters` — registry of libcst-based
  :class:`libcst.CSTTransformer` subclasses. Each rewriter owns one
  migration shape.
* :mod:`llm_code.migrate.v12.diagnostics` — aggregates unsupported
  patterns encountered by the rewriters for end-of-run reporting.
"""
from __future__ import annotations

__all__: list[str] = []

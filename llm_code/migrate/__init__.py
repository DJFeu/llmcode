"""llm_code.migrate — source-rewriting codemod CLIs.

Top-level package. Currently hosts the ``v12`` codemod (see
``llm_code.migrate.v12``) plus the :mod:`llm_code.migrate.cli` entry
point that exposes the standalone click group ``migrate``.

The codemod is intentionally shipped as a *separate* click group and is
**not** wired into the main ``llmcode`` CLI (see plan
``docs/superpowers/plans/2026-04-21-llm-code-v12-plugin-migration.md``
§Task 8.a.2 — wiring into the main CLI is deferred to a later round).
"""
from __future__ import annotations

__all__ = ["cli"]

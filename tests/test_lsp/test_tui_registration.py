"""Smoke test: AppState.from_config wires up all eight LSP tools.

Pre-M10.3 the LSP registration block lived in ``tui/runtime_init.py``.
M10.3 moved it into ``runtime/app_state.py`` alongside every other
subsystem. The invariant — all eight LSP tool classes appear in the
registration block — is unchanged.
"""
from __future__ import annotations

import inspect

import llm_code.runtime.app_state as app_state


def test_app_state_registers_all_eight_lsp_tools() -> None:
    src = inspect.getsource(app_state)
    for cls in (
        "LspGotoDefinitionTool",
        "LspFindReferencesTool",
        "LspDiagnosticsTool",
        "LspHoverTool",
        "LspDocumentSymbolTool",
        "LspWorkspaceSymbolTool",
        "LspImplementationTool",
        "LspCallHierarchyTool",
    ):
        assert cls in src, f"{cls} missing from app_state LSP registration"

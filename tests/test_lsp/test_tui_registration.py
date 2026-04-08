"""Smoke test: TUI startup wires up all six LSP tools."""
from __future__ import annotations

import inspect

import llm_code.tui.app as tui_app


def test_tui_app_imports_all_six_lsp_tools() -> None:
    src = inspect.getsource(tui_app)
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
        assert cls in src, f"{cls} missing from tui.app LSP registration"

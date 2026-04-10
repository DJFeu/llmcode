"""Smoke test: TUI startup wires up all six LSP tools."""
from __future__ import annotations

import inspect

import llm_code.tui.runtime_init as runtime_init


def test_tui_app_imports_all_six_lsp_tools() -> None:
    src = inspect.getsource(runtime_init)
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
        assert cls in src, f"{cls} missing from tui.runtime_init LSP registration"

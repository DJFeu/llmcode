"""MCP approval dialog — review tools/resources from a newly registered server.

Pure logic builder; the Textual modal wiring will be added once the MCP
manager fires per-registration events. For now this module exposes the
`McpApprovalRequest` value object and a helper that filters which items
the user has approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class McpItem:
    kind: str   # "tool" | "resource"
    name: str
    description: str = ""


@dataclass
class McpApprovalRequest:
    server_name: str
    items: list[McpItem] = field(default_factory=list)
    approved: set[str] = field(default_factory=set)

    def approve(self, name: str) -> None:
        self.approved.add(name)

    def approve_all(self) -> None:
        for item in self.items:
            self.approved.add(item.name)

    def is_approved(self, name: str) -> bool:
        return name in self.approved

    def approved_items(self) -> list[McpItem]:
        return [i for i in self.items if i.name in self.approved]


# MCP approval is now handled via TextualDialogs modal in
# LLMCodeApp._show_mcp_approval_dialog, triggered automatically by
# ConversationRuntime.request_mcp_approval → set_mcp_event_sink.

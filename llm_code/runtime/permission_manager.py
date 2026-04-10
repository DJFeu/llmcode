"""Extracted permission management from ConversationRuntime."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from llm_code.logging import get_logger

if TYPE_CHECKING:
    from llm_code.runtime.permissions import PermissionPolicy
    from llm_code.runtime.session import Session

logger = get_logger(__name__)


class PermissionManager:
    """Manages MCP approval flow, session-scoped permission allowlists, and
    interactive permission prompts.

    Extracted from :class:`ConversationRuntime` so that permission-related
    state and logic live in a focused module.
    """

    def __init__(
        self,
        permission_policy: "PermissionPolicy",
        session: "Session",
        *,
        context: Any = None,
    ) -> None:
        self._policy = permission_policy
        self._session = session
        self._context = context

        # MCP approval state
        self._mcp_approval_callback: Any = None
        self._mcp_event_sink: Any = None
        self._mcp_approval_future: asyncio.Future[str] | None = None
        self._mcp_approval_pending: bool = False
        self._mcp_approved_servers: set[str] = set()

        # Interactive permission prompt
        self._permission_future: asyncio.Future[str] | None = None

        # Per-session in-memory permission allowlists
        self._session_allowed_tools: set[str] = set()
        self._session_allowed_exact: set[tuple[str, str]] = set()
        self._session_allowed_prefixes: set[str] = set()
        self._session_allowed_path_roots: set[str] = set()

    # ------------------------------------------------------------------
    # MCP approval
    # ------------------------------------------------------------------

    def set_mcp_approval_callback(self, callback: Any) -> None:
        """Install a callback used to approve non-root MCP spawns."""
        self._mcp_approval_callback = callback

    def set_mcp_event_sink(self, sink: Any) -> None:
        """Install a sink callable that receives out-of-band MCP events."""
        self._mcp_event_sink = sink

    def send_mcp_approval_response(self, response: str) -> None:
        """Resolve a pending MCP approval prompt with 'allow', 'always', or 'deny'."""
        fut = self._mcp_approval_future
        if fut is not None and not fut.done():
            fut.set_result(response)

    async def request_mcp_approval(self, request: Any) -> bool:
        """Ask the attached UI to approve *request*; default-deny if none."""
        # Legacy callback path (tests + custom integrations).
        callback = self._mcp_approval_callback
        if callback is not None:
            try:
                return bool(await callback(request))
            except Exception:  # noqa: BLE001
                return False

        # Extract a server name from the request shape.
        server_name = ""
        owner_agent_id = ""
        description = ""
        if hasattr(request, "server_names") and request.server_names:
            server_name = request.server_names[0]
        elif hasattr(request, "server_name"):
            server_name = request.server_name
        if hasattr(request, "agent_name"):
            owner_agent_id = request.agent_name
        elif hasattr(request, "owner_agent_id"):
            owner_agent_id = request.owner_agent_id
        if hasattr(request, "reason"):
            description = request.reason or ""

        # In-session allowlist short-circuit.
        if server_name and server_name in self._mcp_approved_servers:
            return True

        sink = self._mcp_event_sink
        if sink is None:
            return False

        from llm_code.api.types import StreamMCPApprovalRequest
        event = StreamMCPApprovalRequest(
            server_name=server_name,
            owner_agent_id=owner_agent_id,
            command="",
            description=description,
        )
        try:
            sink(event)
        except Exception:  # noqa: BLE001
            logger.warning("mcp approval sink raised", exc_info=True)
            return False

        loop = asyncio.get_running_loop()
        self._mcp_approval_future = loop.create_future()
        self._mcp_approval_pending = True
        try:
            response = await asyncio.wait_for(
                self._mcp_approval_future, timeout=120,
            )
        except asyncio.TimeoutError:
            response = "deny"
        finally:
            self._mcp_approval_future = None
            self._mcp_approval_pending = False

        if response in ("allow", "always"):
            if response == "always" and server_name:
                self._mcp_approved_servers.add(server_name)
            return True
        return False

    # ------------------------------------------------------------------
    # Session-scoped permission allowlist
    # ------------------------------------------------------------------

    def is_session_allowed(
        self, tool_name: str, args_preview: str, validated_args: dict | None = None,
    ) -> bool:
        """Return True if a tool call is pre-approved by the in-session allowlist."""
        if tool_name in self._session_allowed_tools:
            return True
        if (tool_name, args_preview) in self._session_allowed_exact:
            return True
        if tool_name == "bash" and validated_args is not None:
            cmd = str(validated_args.get("command", "")).strip()
            for prefix in self._session_allowed_prefixes:
                if cmd.startswith(prefix):
                    return True
        if tool_name in ("edit_file", "write_file", "multi_edit") and validated_args is not None:
            path = str(validated_args.get("path") or validated_args.get("file_path") or "")
            for root in self._session_allowed_path_roots:
                if path.startswith(root):
                    return True
        return False

    def record_permission_choice(
        self,
        choice: str,
        tool_name: str,
        args_preview: str,
        validated_args: dict | None = None,
    ) -> None:
        """Persist an 'always' permission choice in the in-session allowlist."""
        if choice == "always_kind":
            if tool_name not in ("edit_file", "write_file", "multi_edit"):
                self._session_allowed_tools.add(tool_name)
            if tool_name == "bash" and validated_args is not None:
                cmd = str(validated_args.get("command", "")).strip()
                first = cmd.split()[0] if cmd else ""
                if first:
                    self._session_allowed_prefixes.add(first + " ")
            if tool_name in ("edit_file", "write_file", "multi_edit"):
                try:
                    self._session_allowed_path_roots.add(str(self._context.cwd))
                except Exception:
                    pass
        elif choice == "always_exact":
            self._session_allowed_exact.add((tool_name, args_preview))

    # ------------------------------------------------------------------
    # Interactive permission prompt
    # ------------------------------------------------------------------

    def send_permission_response(self, response: str, *, edited_args: dict | None = None) -> None:
        """Resolve the pending permission prompt."""
        if self._permission_future is not None and not self._permission_future.done():
            if response == "edit" and edited_args is not None:
                self._permission_future.set_result(f"edit:{json.dumps(edited_args)}")
            else:
                self._permission_future.set_result(response)

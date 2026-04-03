"""High-level IDE bridge API with graceful fallback."""
from __future__ import annotations

import logging
from typing import Any

from llm_code.runtime.config import IDEConfig
from llm_code.ide.server import IDEServer, JsonRpcError

logger = logging.getLogger(__name__)


class IDEBridge:
    """High-level API for IDE communication. Degrades silently when disconnected."""

    def __init__(self, config: IDEConfig) -> None:
        self._config = config
        self._server: IDEServer | None = None

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    @property
    def is_connected(self) -> bool:
        if self._server is None:
            return False
        return self._server.is_running and len(self._server.connected_ides) > 0

    async def start(self) -> None:
        """Start the WebSocket server if IDE integration is enabled."""
        if not self._config.enabled:
            return
        self._server = IDEServer(port=self._config.port)
        await self._server.start()
        logger.info("IDE bridge listening on port %d", self._server.actual_port)

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self._server is not None:
            await self._server.stop()
            self._server = None

    async def open_file(self, path: str, line: int | None = None) -> bool:
        """Ask the IDE to open a file. Returns False on failure."""
        params: dict[str, Any] = {"path": path}
        if line is not None:
            params["line"] = line
        result = await self._safe_request("ide/openFile", params)
        return result is not None and result.get("ok", False)

    async def get_diagnostics(self, path: str) -> list[dict]:
        """Get diagnostics for a file from the IDE. Returns [] on failure."""
        result = await self._safe_request("ide/diagnostics", {"path": path})
        if result is None:
            return []
        return result.get("diagnostics", [])

    async def get_selection(self) -> dict | None:
        """Get the current editor selection. Returns None on failure."""
        return await self._safe_request("ide/selection", {})

    async def show_diff(self, path: str, old_text: str, new_text: str) -> bool:
        """Ask the IDE to show a diff. Returns False on failure."""
        result = await self._safe_request("ide/showDiff", {
            "path": path,
            "old_text": old_text,
            "new_text": new_text,
        })
        return result is not None and result.get("ok", False)

    async def _safe_request(self, method: str, params: dict) -> dict | None:
        """Send a request, returning None on any failure."""
        if self._server is None or not self._server.is_running:
            return None
        try:
            return await self._server.send_request(method, params)
        except (JsonRpcError, OSError, Exception) as exc:
            logger.debug("IDE request %s failed: %s", method, exc)
            return None

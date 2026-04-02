"""LSP server manager: lifecycle management for multiple language servers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_code.lsp.client import LspClient, LspServerConfig, StdioLspTransport


class LspServerManager:
    """Manages a pool of running LSP clients, keyed by language."""

    def __init__(self) -> None:
        self._clients: dict[str, LspClient] = {}

    async def start_server(
        self, name: str, config: LspServerConfig, root_path: Path
    ) -> LspClient:
        """Start a single LSP server and return a connected LspClient.

        *name* is used as the key (typically the language name).
        """
        transport = StdioLspTransport(command=config.command, args=config.args)
        await transport.start()
        client = LspClient(transport)
        root_uri = root_path.as_uri()
        await client.initialize(root_uri)
        self._clients[name] = client
        return client

    async def start_all(
        self, configs: dict[str, LspServerConfig], root_path: Path
    ) -> None:
        """Start all servers from a language -> config mapping."""
        for name, config in configs.items():
            await self.start_server(name, config, root_path)

    async def stop_all(self) -> None:
        """Shutdown all running servers gracefully."""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.shutdown()
            except Exception:
                pass

    def get_client(self, language: str) -> LspClient | None:
        """Return the LspClient for *language*, or None if not running."""
        return self._clients.get(language)

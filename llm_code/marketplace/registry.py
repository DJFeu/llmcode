"""Plugin registry system — search adapters for official, npm, GitHub and custom sources."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PluginSummary:
    """Lightweight summary returned from registry search results."""

    name: str
    description: str
    registry: str
    version: str = ""


@dataclass(frozen=True)
class PluginDetails:
    """Detailed information about a single plugin."""

    name: str
    description: str
    version: str = ""
    repository: str = ""
    install_command: str = ""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PluginRegistry(ABC):
    """Abstract base class for all plugin registries."""

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        """Search the registry and return matching summaries."""
        ...

    @abstractmethod
    async def get_details(self, name: str) -> PluginDetails | None:
        """Fetch detailed information about a named plugin."""
        ...


# ---------------------------------------------------------------------------
# Official MCP registry
# ---------------------------------------------------------------------------

class OfficialRegistry(PluginRegistry):
    """Adapter for registry.modelcontextprotocol.io."""

    BASE_URL = "https://registry.modelcontextprotocol.io"

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/v0/servers",
                    params={"q": query},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
        except Exception:
            return []

        results: list[PluginSummary] = []
        for server in data.get("servers", []):
            name: str = server.get("name", "")
            description: str = server.get("description", "")
            # Filter by query match when the API doesn't do server-side filtering
            if query.lower() in name.lower() or query.lower() in description.lower():
                results.append(
                    PluginSummary(
                        name=name,
                        description=description,
                        registry="official",
                        version=str(server.get("version", "")),
                    )
                )
                if len(results) >= limit:
                    break
        return results

    async def get_details(self, name: str) -> PluginDetails | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/v0/servers/{name}",
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        return PluginDetails(
            name=data.get("name", name),
            description=data.get("description", ""),
            version=str(data.get("version", "")),
            repository=str(data.get("repository", "")),
        )


# ---------------------------------------------------------------------------
# Smithery registry
# ---------------------------------------------------------------------------

class SmitheryRegistry(PluginRegistry):
    """Adapter for api.smithery.ai."""

    BASE_URL = "https://api.smithery.ai"

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/v1/servers",
                    params={"q": query},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        results: list[PluginSummary] = []
        for server in data.get("servers", []):
            results.append(
                PluginSummary(
                    name=server.get("name", ""),
                    description=server.get("description", ""),
                    registry="smithery",
                    version=str(server.get("version", "")),
                )
            )
            if len(results) >= limit:
                break
        return results

    async def get_details(self, name: str) -> PluginDetails | None:
        return None


# ---------------------------------------------------------------------------
# npm registry
# ---------------------------------------------------------------------------

class NpmRegistry(PluginRegistry):
    """Adapter for the npm registry search API."""

    BASE_URL = "https://registry.npmjs.org"

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/-/v1/search",
                    params={"text": f"mcp {query}", "size": limit},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        results: list[PluginSummary] = []
        for obj in data.get("objects", []):
            pkg = obj.get("package", {})
            results.append(
                PluginSummary(
                    name=pkg.get("name", ""),
                    description=pkg.get("description", ""),
                    registry="npm",
                    version=str(pkg.get("version", "")),
                )
            )
        return results

    async def get_details(self, name: str) -> PluginDetails | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/{name}",
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        latest = data.get("dist-tags", {}).get("latest", "")
        version_data = data.get("versions", {}).get(latest, {})
        return PluginDetails(
            name=data.get("name", name),
            description=data.get("description", ""),
            version=latest,
            repository=str(version_data.get("repository", {}).get("url", "")),
            install_command=f"npm install {name}",
        )


# ---------------------------------------------------------------------------
# GitHub marketplace registry
# ---------------------------------------------------------------------------

class GitHubRegistry(PluginRegistry):
    """Adapter for a GitHub-hosted marketplace.json file."""

    def __init__(self, repo: str) -> None:
        """Initialize with a 'owner/repo' string."""
        self._repo = repo

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        url = f"https://raw.githubusercontent.com/{self._repo}/main/marketplace.json"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        results: list[PluginSummary] = []
        for plugin in data.get("plugins", []):
            name: str = plugin.get("name", "")
            description: str = plugin.get("description", "")
            if query.lower() in name.lower() or query.lower() in description.lower():
                results.append(
                    PluginSummary(
                        name=name,
                        description=description,
                        registry="github",
                        version=str(plugin.get("version", "")),
                    )
                )
                if len(results) >= limit:
                    break
        return results

    async def get_details(self, name: str) -> PluginDetails | None:
        url = f"https://raw.githubusercontent.com/{self._repo}/main/marketplace.json"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        for plugin in data.get("plugins", []):
            if plugin.get("name") == name:
                return PluginDetails(
                    name=plugin.get("name", ""),
                    description=plugin.get("description", ""),
                    version=str(plugin.get("version", "")),
                    repository=str(plugin.get("repository", "")),
                )
        return None


# ---------------------------------------------------------------------------
# Custom URL registry
# ---------------------------------------------------------------------------

class CustomRegistry(PluginRegistry):
    """Adapter for an arbitrary URL serving a plugins[] JSON payload."""

    def __init__(self, url: str) -> None:
        self._url = url

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self._url, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        results: list[PluginSummary] = []
        for plugin in data.get("plugins", []):
            name: str = plugin.get("name", "")
            description: str = plugin.get("description", "")
            if query.lower() in name.lower() or query.lower() in description.lower():
                results.append(
                    PluginSummary(
                        name=name,
                        description=description,
                        registry="custom",
                        version=str(plugin.get("version", "")),
                    )
                )
                if len(results) >= limit:
                    break
        return results

    async def get_details(self, name: str) -> PluginDetails | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self._url, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        for plugin in data.get("plugins", []):
            if plugin.get("name") == name:
                return PluginDetails(
                    name=plugin.get("name", ""),
                    description=plugin.get("description", ""),
                    version=str(plugin.get("version", "")),
                )
        return None


# ---------------------------------------------------------------------------
# Unified registry — parallel search across all registered sources
# ---------------------------------------------------------------------------

class UnifiedRegistry(PluginRegistry):
    """Aggregates multiple registries and searches them in parallel."""

    def __init__(self, registries: dict[str, PluginRegistry]) -> None:
        self._registries = registries

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        if not self._registries:
            return []

        tasks = [reg.search(query, limit) for reg in self._registries.values()]
        all_results: list[list[PluginSummary]] = await asyncio.gather(*tasks)

        # Merge and deduplicate by name (first occurrence wins)
        seen: set[str] = set()
        merged: list[PluginSummary] = []
        for batch in all_results:
            for summary in batch:
                if summary.name not in seen:
                    seen.add(summary.name)
                    merged.append(summary)

        return merged

    async def get_details(self, name: str) -> PluginDetails | None:
        for reg in self._registries.values():
            details = await reg.get_details(name)
            if details is not None:
                return details
        return None

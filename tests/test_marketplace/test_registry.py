"""Tests for llm_code.marketplace.registry — TDD: written before implementation."""
from __future__ import annotations


import httpx
import pytest
import respx

from llm_code.marketplace.registry import (
    CustomRegistry,
    NpmRegistry,
    OfficialRegistry,
    PluginDetails,
    PluginRegistry,
    PluginSummary,
    UnifiedRegistry,
)


# ---------------------------------------------------------------------------
# PluginSummary
# ---------------------------------------------------------------------------

class TestPluginSummary:
    def test_creation(self):
        ps = PluginSummary(name="my-plugin", description="A plugin", registry="npm")
        assert ps.name == "my-plugin"
        assert ps.description == "A plugin"
        assert ps.registry == "npm"
        assert ps.version == ""

    def test_with_version(self):
        ps = PluginSummary(name="p", description="d", registry="r", version="1.2.3")
        assert ps.version == "1.2.3"

    def test_frozen(self):
        import dataclasses
        ps = PluginSummary(name="p", description="d", registry="r")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ps.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PluginRegistry ABC
# ---------------------------------------------------------------------------

class TestPluginRegistryABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            PluginRegistry()  # type: ignore[abstract]

    def test_has_search_method(self):
        assert hasattr(PluginRegistry, "search")

    def test_has_get_details_method(self):
        assert hasattr(PluginRegistry, "get_details")


# ---------------------------------------------------------------------------
# OfficialRegistry
# ---------------------------------------------------------------------------

class TestOfficialRegistry:
    @pytest.mark.asyncio
    async def test_search_returns_summaries(self):
        mock_response = {
            "servers": [
                {
                    "name": "mcp-github",
                    "description": "GitHub MCP server",
                    "version": "1.0.0",
                },
                {
                    "name": "mcp-filesystem",
                    "description": "Filesystem MCP server",
                    "version": "2.0.0",
                },
            ]
        }
        with respx.mock(base_url="https://registry.modelcontextprotocol.io") as mock:
            mock.get("/v0/servers").mock(
                return_value=httpx.Response(200, json=mock_response)
            )
            registry = OfficialRegistry()
            results = await registry.search("github")

        assert len(results) >= 1
        names = [r.name for r in results]
        assert "mcp-github" in names
        for r in results:
            assert r.registry == "official"

    @pytest.mark.asyncio
    async def test_search_graceful_on_error(self):
        with respx.mock(base_url="https://registry.modelcontextprotocol.io") as mock:
            mock.get("/v0/servers").mock(
                return_value=httpx.Response(500)
            )
            registry = OfficialRegistry()
            results = await registry.search("anything")

        assert results == []


# ---------------------------------------------------------------------------
# NpmRegistry
# ---------------------------------------------------------------------------

class TestNpmRegistry:
    @pytest.mark.asyncio
    async def test_search_returns_summaries(self):
        mock_response = {
            "objects": [
                {
                    "package": {
                        "name": "@mcp/npm-server",
                        "description": "An npm MCP server",
                        "version": "0.5.0",
                    }
                },
                {
                    "package": {
                        "name": "@mcp/other-server",
                        "description": "Another npm MCP server",
                        "version": "1.0.0",
                    }
                },
            ]
        }
        with respx.mock(base_url="https://registry.npmjs.org") as mock:
            mock.get("/-/v1/search").mock(
                return_value=httpx.Response(200, json=mock_response)
            )
            registry = NpmRegistry()
            results = await registry.search("mcp-server")

        assert len(results) == 2
        for r in results:
            assert r.registry == "npm"

    @pytest.mark.asyncio
    async def test_search_graceful_on_error(self):
        with respx.mock(base_url="https://registry.npmjs.org") as mock:
            mock.get("/-/v1/search").mock(
                return_value=httpx.Response(503)
            )
            registry = NpmRegistry()
            results = await registry.search("anything")

        assert results == []


# ---------------------------------------------------------------------------
# CustomRegistry
# ---------------------------------------------------------------------------

class TestCustomRegistry:
    @pytest.mark.asyncio
    async def test_search_returns_summaries(self):
        custom_url = "https://my-custom-registry.example.com/plugins.json"
        mock_response = {
            "plugins": [
                {"name": "custom-plugin-a", "description": "Plugin A", "version": "1.0.0"},
                {"name": "custom-plugin-b", "description": "Plugin B", "version": "2.0.0"},
            ]
        }
        with respx.mock() as mock:
            mock.get(custom_url).mock(
                return_value=httpx.Response(200, json=mock_response)
            )
            registry = CustomRegistry(custom_url)
            results = await registry.search("custom")

        assert len(results) >= 1
        for r in results:
            assert r.registry == "custom"

    @pytest.mark.asyncio
    async def test_search_graceful_on_error(self):
        custom_url = "https://broken-registry.example.com/plugins.json"
        with respx.mock() as mock:
            mock.get(custom_url).mock(
                return_value=httpx.Response(404)
            )
            registry = CustomRegistry(custom_url)
            results = await registry.search("anything")

        assert results == []


# ---------------------------------------------------------------------------
# UnifiedRegistry
# ---------------------------------------------------------------------------

class FakeRegistry(PluginRegistry):
    """In-memory registry for testing."""

    def __init__(self, name: str, plugins: list[dict[str, str]]) -> None:
        self._name = name
        self._plugins = plugins

    async def search(self, query: str, limit: int = 20) -> list[PluginSummary]:
        return [
            PluginSummary(
                name=p["name"],
                description=p.get("description", ""),
                registry=self._name,
                version=p.get("version", ""),
            )
            for p in self._plugins
            if query.lower() in p["name"].lower() or query.lower() in p.get("description", "").lower()
        ]

    async def get_details(self, name: str) -> PluginDetails | None:
        for p in self._plugins:
            if p["name"] == name:
                return PluginDetails(name=p["name"], description=p.get("description", ""))
        return None


class TestUnifiedRegistry:
    @pytest.mark.asyncio
    async def test_merges_results_from_multiple_registries(self):
        reg_a = FakeRegistry("source-a", [
            {"name": "plugin-alpha", "description": "alpha plugin"},
            {"name": "plugin-beta", "description": "beta plugin"},
        ])
        reg_b = FakeRegistry("source-b", [
            {"name": "plugin-gamma", "description": "gamma plugin"},
            {"name": "plugin-alpha", "description": "alpha from b"},  # duplicate name
        ])

        unified = UnifiedRegistry({"source-a": reg_a, "source-b": reg_b})
        results = await unified.search("plugin")

        names = [r.name for r in results]
        assert "plugin-alpha" in names
        assert "plugin-beta" in names
        assert "plugin-gamma" in names

    @pytest.mark.asyncio
    async def test_search_with_no_matches(self):
        reg = FakeRegistry("empty", [{"name": "unrelated", "description": "nothing"}])
        unified = UnifiedRegistry({"empty": reg})
        results = await unified.search("xyznonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_unified_registry_is_registry(self):
        unified = UnifiedRegistry({})
        assert isinstance(unified, PluginRegistry)

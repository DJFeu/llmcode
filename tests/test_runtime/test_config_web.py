"""Tests for WebFetchConfig and WebSearchConfig in RuntimeConfig."""
from __future__ import annotations

import dataclasses

import pytest

from llm_code.runtime.config import (
    RuntimeConfig,
    WebFetchConfig,
    WebSearchConfig,
)


class TestWebFetchConfig:
    def test_defaults(self) -> None:
        cfg = WebFetchConfig()
        assert cfg.default_renderer == "default"
        assert cfg.browser_timeout == 30.0
        assert cfg.cache_ttl == 900.0
        assert cfg.cache_max_entries == 50
        assert cfg.max_length == 50_000

    def test_frozen(self) -> None:
        cfg = WebFetchConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.default_renderer = "browser"  # type: ignore[misc]

    def test_custom_values(self) -> None:
        cfg = WebFetchConfig(
            default_renderer="browser",
            browser_timeout=60.0,
            cache_ttl=300.0,
            cache_max_entries=100,
            max_length=10_000,
        )
        assert cfg.default_renderer == "browser"
        assert cfg.browser_timeout == 60.0
        assert cfg.cache_ttl == 300.0
        assert cfg.cache_max_entries == 100
        assert cfg.max_length == 10_000


class TestWebSearchConfig:
    def test_defaults(self) -> None:
        cfg = WebSearchConfig()
        assert cfg.default_backend == "duckduckgo"
        assert cfg.tavily_api_key_env == "TAVILY_API_KEY"
        assert cfg.serper_api_key_env == "SERPER_API_KEY"
        assert cfg.exa_api_key_env == "EXA_API_KEY"
        assert cfg.searxng_base_url == ""
        assert cfg.max_results == 10
        assert cfg.domain_allowlist == ()
        assert cfg.domain_denylist == ()

    def test_frozen(self) -> None:
        cfg = WebSearchConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.default_backend = "tavily"  # type: ignore[misc]

    def test_custom_values(self) -> None:
        cfg = WebSearchConfig(
            default_backend="tavily",
            tavily_api_key_env="MY_TAVILY_KEY",
            searxng_base_url="http://searxng.local",
            max_results=5,
            domain_allowlist=("example.com", "docs.python.org"),
            domain_denylist=("spam.com",),
        )
        assert cfg.default_backend == "tavily"
        assert cfg.tavily_api_key_env == "MY_TAVILY_KEY"
        assert cfg.searxng_base_url == "http://searxng.local"
        assert cfg.max_results == 5
        assert cfg.domain_allowlist == ("example.com", "docs.python.org")
        assert cfg.domain_denylist == ("spam.com",)

    def test_domain_lists_are_tuples(self) -> None:
        cfg = WebSearchConfig()
        assert isinstance(cfg.domain_allowlist, tuple)
        assert isinstance(cfg.domain_denylist, tuple)


class TestRuntimeConfigWebFields:
    def test_runtime_config_has_web_fetch_field(self) -> None:
        cfg = RuntimeConfig()
        assert hasattr(cfg, "web_fetch")
        assert isinstance(cfg.web_fetch, WebFetchConfig)

    def test_runtime_config_has_web_search_field(self) -> None:
        cfg = RuntimeConfig()
        assert hasattr(cfg, "web_search")
        assert isinstance(cfg.web_search, WebSearchConfig)

    def test_web_fetch_uses_defaults(self) -> None:
        cfg = RuntimeConfig()
        assert cfg.web_fetch == WebFetchConfig()

    def test_web_search_uses_defaults(self) -> None:
        cfg = RuntimeConfig()
        assert cfg.web_search == WebSearchConfig()

    def test_runtime_config_allows_custom_web_fetch(self) -> None:
        custom = WebFetchConfig(max_length=1000)
        cfg = RuntimeConfig(web_fetch=custom)
        assert cfg.web_fetch.max_length == 1000

    def test_runtime_config_allows_custom_web_search(self) -> None:
        custom = WebSearchConfig(max_results=3)
        cfg = RuntimeConfig(web_search=custom)
        assert cfg.web_search.max_results == 3

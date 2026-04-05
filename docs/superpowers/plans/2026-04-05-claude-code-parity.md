# Claude Code Parity Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 high-priority features to LLM-Code that close the gap with Claude Code: WebFetch, WebSearch, per-agent model override, plan mode, and git worktree backend.

**Architecture:** Two phases — Phase 1 adds independent tools/config (WebFetch, WebSearch, per-agent model), Phase 2 modifies core systems (plan mode in permissions/conversation loop, worktree backend in swarm). All new code follows existing frozen-dataclass + Tool ABC patterns.

**Tech Stack:** Python 3.11+, httpx, readability-lxml, html2text, optional playwright, pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-05-claude-code-parity-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `llm_code/tools/web_common.py` | URL safety classification, URL cache, HTML-to-markdown extraction |
| `llm_code/tools/web_fetch.py` | WebFetch tool (fetch URL to markdown) |
| `llm_code/tools/web_search.py` | WebSearch tool (query to results) |
| `llm_code/tools/search_backends/__init__.py` | SearchBackend protocol, SearchResult dataclass, factory function |
| `llm_code/tools/search_backends/duckduckgo.py` | DuckDuckGo backend (default) |
| `llm_code/tools/search_backends/tavily.py` | Tavily backend (API key) |
| `llm_code/tools/search_backends/searxng.py` | SearXNG backend (self-hosted) |
| `llm_code/swarm/backend_worktree.py` | Git worktree backend for swarm agents |
| `llm_code/runtime/plan.py` | PlanEntry, PlanSummary, summarize_tool_call |
| `tests/test_tools/test_web_common.py` | Tests for URL safety + cache + extraction |
| `tests/test_tools/test_web_fetch.py` | Tests for WebFetch tool |
| `tests/test_tools/test_web_search.py` | Tests for WebSearch tool |
| `tests/test_tools/test_search_backends/` | Tests for each search backend |
| `tests/test_swarm/test_backend_worktree.py` | Tests for worktree backend |
| `tests/test_runtime/test_plan.py` | Tests for plan data structures |

### Modified Files

| File | Change |
|------|--------|
| `llm_code/runtime/config.py:73-76` | Expand SwarmConfig with role_models + WorktreeConfig |
| `llm_code/runtime/config.py:100-134` | Add web_fetch + web_search fields to RuntimeConfig |
| `llm_code/runtime/permissions.py:13-18` | Add PLAN to PermissionMode enum |
| `llm_code/runtime/permissions.py:21-24` | Add NEED_PLAN to PermissionOutcome enum |
| `llm_code/runtime/permissions.py:125-177` | Add PLAN branch in authorize() |
| `llm_code/swarm/manager.py:42-88` | Add model param to create_member, add _resolve_model |
| `llm_code/swarm/manager.py:117-129` | Add worktree to _resolve_backend |
| `llm_code/swarm/backend_subprocess.py:22-67` | Add model param to spawn |
| `llm_code/swarm/backend_tmux.py:24-63` | Add model param to spawn |
| `llm_code/tools/swarm_create.py:12-15` | Add model field to SwarmCreateInput |
| `llm_code/tools/swarm_create.py:35-55` | Add model to input_schema |
| `pyproject.toml:38-57` | Add web + web-browser optional dependencies |

---

## Phase 1: Independent Features

---

### Task 1: URL Safety Classification (web_common.py part 1)

**Files:**
- Create: `llm_code/tools/web_common.py`
- Test: `tests/test_tools/test_web_common.py`

- [ ] **Step 1: Write failing tests for URL safety**

```python
# tests/test_tools/test_web_common.py
import pytest
from llm_code.tools.web_common import UrlSafetyResult, classify_url


class TestClassifyUrl:
    def test_safe_https(self):
        result = classify_url("https://docs.python.org/3/library/asyncio.html")
        assert result.classification == "safe"
        assert result.reasons == ()

    def test_safe_http(self):
        result = classify_url("http://example.com")
        assert result.classification == "safe"

    def test_blocked_file_scheme(self):
        result = classify_url("file:///etc/passwd")
        assert result.classification == "blocked"
        assert "file://" in result.reasons[0]

    def test_blocked_private_ip_10(self):
        result = classify_url("http://10.0.0.1/admin")
        assert result.classification == "blocked"
        assert "private" in result.reasons[0].lower()

    def test_blocked_private_ip_172(self):
        result = classify_url("http://172.16.0.1/api")
        assert result.classification == "blocked"

    def test_blocked_private_ip_192(self):
        result = classify_url("http://192.168.1.1/config")
        assert result.classification == "blocked"

    def test_blocked_loopback_ipv6(self):
        result = classify_url("http://[::1]/api")
        assert result.classification == "blocked"

    def test_blocked_cloud_metadata(self):
        result = classify_url("http://169.254.169.254/latest/meta-data/")
        assert result.classification == "blocked"
        assert "metadata" in result.reasons[0].lower()

    def test_blocked_metadata_google(self):
        result = classify_url("http://metadata.google.internal/computeMetadata/v1/")
        assert result.classification == "blocked"

    def test_needs_confirm_localhost(self):
        result = classify_url("http://localhost:8080/api")
        assert result.classification == "needs_confirm"

    def test_needs_confirm_127(self):
        result = classify_url("http://127.0.0.1:3000/health")
        assert result.classification == "needs_confirm"

    def test_needs_confirm_ip_only(self):
        result = classify_url("http://8.8.8.8/dns")
        assert result.classification == "needs_confirm"

    def test_needs_confirm_nonstandard_port(self):
        result = classify_url("https://example.com:9999/api")
        assert result.classification == "needs_confirm"

    def test_safe_standard_ports(self):
        assert classify_url("http://example.com:80/").classification == "safe"
        assert classify_url("https://example.com:443/").classification == "safe"

    def test_invalid_url(self):
        result = classify_url("not a url")
        assert result.classification == "blocked"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement URL safety classification**

```python
# llm_code/tools/web_common.py
"""Shared utilities for web tools: URL safety, caching, content extraction."""

from __future__ import annotations

import dataclasses
import ipaddress
import re
import time
from collections import OrderedDict
from urllib.parse import urlparse


@dataclasses.dataclass(frozen=True)
class UrlSafetyResult:
    classification: str  # "safe" | "needs_confirm" | "blocked"
    reasons: tuple[str, ...] = ()

    @property
    def is_safe(self) -> bool:
        return self.classification == "safe"

    @property
    def is_blocked(self) -> bool:
        return self.classification == "blocked"

    @property
    def needs_confirm(self) -> bool:
        return self.classification == "needs_confirm"


_METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.com",
})

_STANDARD_PORTS = frozenset({80, 443, None})


def _is_private_ip(host: str) -> bool:
    cleaned = host.strip("[]")
    try:
        addr = ipaddress.ip_address(cleaned)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def _is_ip_address(host: str) -> bool:
    cleaned = host.strip("[]")
    try:
        ipaddress.ip_address(cleaned)
        return True
    except ValueError:
        return False


def classify_url(url: str) -> UrlSafetyResult:
    try:
        parsed = urlparse(url)
    except Exception:
        return UrlSafetyResult("blocked", ("Invalid URL",))

    if not parsed.scheme or not parsed.hostname:
        return UrlSafetyResult("blocked", ("Invalid URL: missing scheme or host",))

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port

    if scheme == "file":
        return UrlSafetyResult("blocked", ("file:// scheme is not allowed",))

    if scheme not in ("http", "https"):
        return UrlSafetyResult("blocked", (f"Unsupported scheme: {scheme}",))

    if host in _METADATA_HOSTS:
        return UrlSafetyResult("blocked", (f"Cloud metadata endpoint blocked: {host}",))

    if _is_private_ip(host) and host not in ("localhost", "127.0.0.1", "::1"):
        return UrlSafetyResult("blocked", (f"Private IP address blocked: {host}",))

    if host in ("localhost", "127.0.0.1") or host == "::1":
        return UrlSafetyResult("needs_confirm", (f"Localhost URL: {host}",))

    if _is_ip_address(host):
        return UrlSafetyResult("needs_confirm", (f"IP-only URL: {host}",))

    if port not in _STANDARD_PORTS:
        return UrlSafetyResult("needs_confirm", (f"Non-standard port: {port}",))

    return UrlSafetyResult("safe")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py -v`
Expected: All 16 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/web_common.py tests/test_tools/test_web_common.py
git commit -m "feat: add URL safety classification for web tools"
```

---

### Task 2: URL Cache (web_common.py part 2)

**Files:**
- Modify: `llm_code/tools/web_common.py`
- Modify: `tests/test_tools/test_web_common.py`

- [ ] **Step 1: Write failing tests for URL cache**

```python
# Append to tests/test_tools/test_web_common.py
from llm_code.tools.web_common import CacheEntry, UrlCache


class TestUrlCache:
    def test_get_miss(self):
        cache = UrlCache(max_entries=5, ttl=900.0)
        assert cache.get("https://example.com") is None

    def test_put_and_get(self):
        cache = UrlCache(max_entries=5, ttl=900.0)
        cache.put("https://example.com", "content here")
        assert cache.get("https://example.com") == "content here"

    def test_ttl_expiry(self):
        cache = UrlCache(max_entries=5, ttl=0.0)
        cache.put("https://example.com", "content")
        assert cache.get("https://example.com") is None

    def test_max_entries_eviction(self):
        cache = UrlCache(max_entries=2, ttl=900.0)
        cache.put("https://a.com", "a")
        cache.put("https://b.com", "b")
        cache.put("https://c.com", "c")
        assert cache.get("https://a.com") is None
        assert cache.get("https://b.com") == "b"
        assert cache.get("https://c.com") == "c"

    def test_put_updates_existing(self):
        cache = UrlCache(max_entries=5, ttl=900.0)
        cache.put("https://example.com", "old")
        cache.put("https://example.com", "new")
        assert cache.get("https://example.com") == "new"

    def test_clear(self):
        cache = UrlCache(max_entries=5, ttl=900.0)
        cache.put("https://example.com", "content")
        cache.clear()
        assert cache.get("https://example.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py::TestUrlCache -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement URL cache**

Append to `llm_code/tools/web_common.py`:

```python
@dataclasses.dataclass(frozen=True)
class CacheEntry:
    content: str
    fetched_at: float
    ttl: float = 900.0

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.fetched_at) >= self.ttl


class UrlCache:
    def __init__(self, max_entries: int = 50, ttl: float = 900.0) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, url: str) -> str | None:
        entry = self._store.get(url)
        if entry is None:
            return None
        if entry.is_expired:
            self._store.pop(url, None)
            return None
        self._store.move_to_end(url)
        return entry.content

    def put(self, url: str, content: str) -> None:
        self._store.pop(url, None)
        if len(self._store) >= self._max_entries:
            self._store.popitem(last=False)
        self._store[url] = CacheEntry(
            content=content,
            fetched_at=time.monotonic(),
            ttl=self._ttl,
        )

    def clear(self) -> None:
        self._store.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py -v`
Expected: All 22 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/web_common.py tests/test_tools/test_web_common.py
git commit -m "feat: add LRU URL cache with TTL for web tools"
```

---

### Task 3: HTML Content Extraction (web_common.py part 3)

**Files:**
- Modify: `llm_code/tools/web_common.py`
- Modify: `tests/test_tools/test_web_common.py`

- [ ] **Step 1: Write failing tests for content extraction**

```python
# Append to tests/test_tools/test_web_common.py
from llm_code.tools.web_common import extract_content


class TestExtractContent:
    def test_html_to_markdown(self):
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        result = extract_content(html, content_type="text/html", raw=False)
        assert "Hello" in result
        assert "World" in result

    def test_html_raw_mode(self):
        html = "<html><head><nav>Menu</nav></head><body><h1>Hello</h1></body></html>"
        result = extract_content(html, content_type="text/html", raw=True)
        assert "Hello" in result

    def test_json_formatting(self):
        json_str = '{"key":"value","nested":{"a":1}}'
        result = extract_content(json_str, content_type="application/json", raw=False)
        assert '"key": "value"' in result
        assert '"a": 1' in result

    def test_plain_text_passthrough(self):
        text = "Plain text content here"
        result = extract_content(text, content_type="text/plain", raw=False)
        assert result == "Plain text content here"

    def test_truncation(self):
        long_text = "A" * 1000
        result = extract_content(long_text, content_type="text/plain", raw=False, max_length=100)
        assert len(result) <= 130
        assert "[truncated]" in result

    def test_empty_content(self):
        result = extract_content("", content_type="text/html", raw=False)
        assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py::TestExtractContent -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement content extraction**

Append to `llm_code/tools/web_common.py`:

```python
import json


def _html_to_markdown(html: str, use_readability: bool = True) -> str:
    if not html.strip():
        return ""

    cleaned_html = html

    if use_readability:
        try:
            from readability import Document
            doc = Document(html)
            cleaned_html = doc.summary()
        except ImportError:
            pass
        except Exception:
            pass

    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        return h.handle(cleaned_html).strip()
    except ImportError:
        return re.sub(r"<[^>]+>", "", cleaned_html).strip()


def extract_content(
    body: str,
    content_type: str,
    raw: bool = False,
    max_length: int = 50_000,
) -> str:
    if not body:
        return ""

    ct = content_type.lower().split(";")[0].strip()

    if ct == "application/json" or ct.endswith("+json"):
        try:
            parsed = json.loads(body)
            content = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            content = body
    elif "html" in ct:
        content = _html_to_markdown(body, use_readability=not raw)
    else:
        content = body

    if len(content) > max_length:
        content = content[:max_length] + "\n\n[truncated]"

    return content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py -v`
Expected: All 28 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/web_common.py tests/test_tools/test_web_common.py
git commit -m "feat: add HTML content extraction with readability + html2text"
```

---

### Task 4: WebFetchConfig + WebSearchConfig in RuntimeConfig

**Files:**
- Modify: `llm_code/runtime/config.py:73-76` and `llm_code/runtime/config.py:100-134`
- Test: `tests/test_runtime/test_config_web.py`

- [ ] **Step 1: Write failing tests for config**

```python
# tests/test_runtime/test_config_web.py
import pytest
from llm_code.runtime.config import WebFetchConfig, WebSearchConfig, RuntimeConfig


class TestWebFetchConfig:
    def test_defaults(self):
        cfg = WebFetchConfig()
        assert cfg.default_renderer == "default"
        assert cfg.browser_timeout == 30.0
        assert cfg.cache_ttl == 900.0
        assert cfg.cache_max_entries == 50
        assert cfg.max_length == 50_000

    def test_frozen(self):
        cfg = WebFetchConfig()
        with pytest.raises(AttributeError):
            cfg.default_renderer = "browser"


class TestWebSearchConfig:
    def test_defaults(self):
        cfg = WebSearchConfig()
        assert cfg.default_backend == "duckduckgo"
        assert cfg.tavily_api_key_env == "TAVILY_API_KEY"
        assert cfg.searxng_base_url == ""
        assert cfg.max_results == 10
        assert cfg.domain_allowlist == ()
        assert cfg.domain_denylist == ()


class TestRuntimeConfigWebFields:
    def test_runtime_has_web_fetch(self):
        rc = RuntimeConfig()
        assert isinstance(rc.web_fetch, WebFetchConfig)

    def test_runtime_has_web_search(self):
        rc = RuntimeConfig()
        assert isinstance(rc.web_search, WebSearchConfig)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_web.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Add config dataclasses**

Add before SwarmConfig in `llm_code/runtime/config.py` (around line 73):

```python
@dataclasses.dataclass(frozen=True)
class WebFetchConfig:
    default_renderer: str = "default"
    browser_timeout: float = 30.0
    cache_ttl: float = 900.0
    cache_max_entries: int = 50
    max_length: int = 50_000


@dataclasses.dataclass(frozen=True)
class WebSearchConfig:
    default_backend: str = "duckduckgo"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    searxng_base_url: str = ""
    max_results: int = 10
    domain_allowlist: tuple[str, ...] = ()
    domain_denylist: tuple[str, ...] = ()
```

Add to RuntimeConfig fields (after telemetry):

```python
    web_fetch: WebFetchConfig = dataclasses.field(default_factory=WebFetchConfig)
    web_search: WebSearchConfig = dataclasses.field(default_factory=WebSearchConfig)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_web.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/runtime/config.py tests/test_runtime/test_config_web.py
git commit -m "feat: add WebFetchConfig + WebSearchConfig to RuntimeConfig"
```

---

### Task 5: WebFetch Tool

**Files:**
- Create: `llm_code/tools/web_fetch.py`
- Test: `tests/test_tools/test_web_fetch.py`

- [ ] **Step 1: Write failing tests for WebFetch tool**

```python
# tests/test_tools/test_web_fetch.py
import pytest
from unittest.mock import patch, MagicMock
from llm_code.tools.web_fetch import WebFetchTool
from llm_code.tools.base import PermissionLevel
from llm_code.runtime.config import WebFetchConfig


class TestWebFetchToolProperties:
    def setup_method(self):
        self.tool = WebFetchTool(config=WebFetchConfig())

    def test_name(self):
        assert self.tool.name == "web_fetch"

    def test_permission(self):
        assert self.tool.required_permission == PermissionLevel.FULL_ACCESS

    def test_not_read_only(self):
        assert self.tool.is_read_only({}) is False

    def test_concurrency_safe(self):
        assert self.tool.is_concurrency_safe({}) is True

    def test_input_schema_has_url(self):
        schema = self.tool.input_schema
        assert "url" in schema["properties"]
        assert "url" in schema["required"]


class TestWebFetchToolExecute:
    def setup_method(self):
        self.tool = WebFetchTool(config=WebFetchConfig())

    def test_blocked_url(self):
        result = self.tool.execute({"url": "file:///etc/passwd"})
        assert result.is_error is True
        assert "blocked" in result.output.lower()

    def test_missing_url(self):
        result = self.tool.execute({})
        assert result.is_error is True

    @patch("llm_code.tools.web_fetch.httpx")
    def test_successful_fetch_html(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><h1>Title</h1><p>Body text</p></body></html>"
        mock_httpx.get.return_value = mock_response

        result = self.tool.execute({"url": "https://example.com"})
        assert result.is_error is False
        assert "Title" in result.output
        assert result.metadata["status_code"] == 200

    @patch("llm_code.tools.web_fetch.httpx")
    def test_successful_fetch_json(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = '{"key": "value"}'
        mock_httpx.get.return_value = mock_response

        result = self.tool.execute({"url": "https://api.example.com/data"})
        assert result.is_error is False
        assert '"key": "value"' in result.output

    @patch("llm_code.tools.web_fetch.httpx")
    def test_http_error(self, mock_httpx):
        mock_httpx.get.side_effect = Exception("Connection refused")
        result = self.tool.execute({"url": "https://example.com"})
        assert result.is_error is True
        assert "Connection refused" in result.output

    @patch("llm_code.tools.web_fetch.httpx")
    def test_cache_hit(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "cached content"
        mock_httpx.get.return_value = mock_response

        self.tool.execute({"url": "https://example.com/cached"})
        self.tool.execute({"url": "https://example.com/cached"})
        assert mock_httpx.get.call_count == 1

    def test_renderer_resolution_auto(self):
        tool = WebFetchTool(config=WebFetchConfig(default_renderer="default"))
        assert tool._resolve_renderer("auto") == "default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_fetch.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement WebFetch tool**

```python
# llm_code/tools/web_fetch.py
"""WebFetch tool: fetch a URL and return content as markdown."""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.web_common import UrlCache, classify_url, extract_content
from llm_code.runtime.config import WebFetchConfig


class WebFetchInput(BaseModel):
    url: str
    prompt: str = ""
    max_length: int = 50_000
    raw: bool = False
    renderer: str = "auto"


class WebFetchTool(Tool):
    def __init__(self, config: WebFetchConfig | None = None) -> None:
        self._config = config or WebFetchConfig()
        self._cache = UrlCache(
            max_entries=self._config.cache_max_entries,
            ttl=self._config.cache_ttl,
        )

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and return its content as markdown. "
            "Supports HTML pages (with readability extraction), JSON APIs, and plain text. "
            "Results are cached for 15 minutes."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "prompt": {"type": "string", "description": "Optional focus hint", "default": ""},
                "max_length": {"type": "integer", "description": "Max content length", "default": 50000},
                "raw": {"type": "boolean", "description": "Skip readability extraction", "default": False},
                "renderer": {
                    "type": "string",
                    "description": "Content renderer: auto, default, or browser",
                    "enum": ["auto", "default", "browser"],
                    "default": "auto",
                },
            },
            "required": ["url"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    @property
    def input_model(self) -> type[WebFetchInput]:
        return WebFetchInput

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def _resolve_renderer(self, requested: str) -> str:
        if requested == "auto":
            requested = self._config.default_renderer
        if requested == "browser":
            try:
                import playwright  # noqa: F401
                return "browser"
            except ImportError:
                return "default"
        return "default"

    def _fetch_with_browser(self, url: str, timeout: float) -> tuple[str, int, str]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            status = response.status if response else 0
            body = page.content()
            browser.close()
        return body, status, "text/html"

    def _fetch_with_httpx(self, url: str) -> tuple[str, int, str]:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "llm-code/1.0 (web-fetch)"},
        )
        ct = response.headers.get("content-type", "text/plain")
        return response.text, response.status_code, ct

    def execute(self, args: dict) -> ToolResult:
        url = args.get("url", "")
        if not url:
            return ToolResult(output="Error: 'url' is required", is_error=True)

        safety = classify_url(url)
        if safety.is_blocked:
            return ToolResult(
                output=f"URL blocked: {'; '.join(safety.reasons)}",
                is_error=True,
                metadata={"url": url, "classification": "blocked"},
            )

        cached = self._cache.get(url)
        if cached is not None:
            return ToolResult(output=cached, metadata={"url": url, "cached": True})

        renderer = self._resolve_renderer(args.get("renderer", "auto"))
        raw = args.get("raw", False)
        max_length = args.get("max_length", self._config.max_length)
        prompt = args.get("prompt", "")

        try:
            if renderer == "browser":
                body, status_code, content_type = self._fetch_with_browser(
                    url, self._config.browser_timeout,
                )
            else:
                body, status_code, content_type = self._fetch_with_httpx(url)
        except Exception as exc:
            return ToolResult(output=f"Fetch error: {exc}", is_error=True, metadata={"url": url})

        content = extract_content(body, content_type, raw=raw, max_length=max_length)
        self._cache.put(url, content)

        metadata = {
            "url": url,
            "status_code": status_code,
            "content_type": content_type.split(";")[0].strip(),
            "cached": False,
        }
        if prompt:
            metadata["prompt"] = prompt

        return ToolResult(output=content, metadata=metadata)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_fetch.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/web_fetch.py tests/test_tools/test_web_fetch.py
git commit -m "feat: add WebFetch tool with dual renderer and cache"
```

---

### Task 6: SearchBackend Protocol + DuckDuckGo Backend

**Files:**
- Create: `llm_code/tools/search_backends/__init__.py`
- Create: `llm_code/tools/search_backends/duckduckgo.py`
- Test: `tests/test_tools/test_search_backends/test_duckduckgo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_search_backends/__init__.py
# (empty)

# tests/test_tools/test_search_backends/test_duckduckgo.py
import pytest
from unittest.mock import patch, MagicMock
from llm_code.tools.search_backends import SearchResult, create_backend
from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend


class TestSearchResult:
    def test_frozen(self):
        r = SearchResult(title="Test", url="https://example.com", snippet="Desc")
        assert r.title == "Test"
        with pytest.raises(AttributeError):
            r.title = "Changed"


class TestCreateBackend:
    def test_duckduckgo(self):
        backend = create_backend("duckduckgo")
        assert isinstance(backend, DuckDuckGoBackend)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown search backend"):
            create_backend("nonexistent")


class TestDuckDuckGoBackend:
    def test_name(self):
        assert DuckDuckGoBackend().name == "duckduckgo"

    @patch("llm_code.tools.search_backends.duckduckgo.httpx")
    def test_search_handles_error(self, mock_httpx):
        mock_httpx.get.side_effect = Exception("Network error")
        results = DuckDuckGoBackend().search("test query")
        assert results == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_search_backends/ -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement protocol + DuckDuckGo**

```python
# llm_code/tools/search_backends/__init__.py
"""Search backend protocol and factory."""

from __future__ import annotations

import dataclasses
from typing import Protocol


@dataclasses.dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    def search(self, query: str, max_results: int = 10) -> tuple[SearchResult, ...]: ...

    @property
    def name(self) -> str: ...


def create_backend(
    backend_name: str,
    *,
    tavily_api_key: str = "",
    searxng_base_url: str = "",
) -> SearchBackend:
    if backend_name == "duckduckgo":
        from llm_code.tools.search_backends.duckduckgo import DuckDuckGoBackend
        return DuckDuckGoBackend()
    elif backend_name == "tavily":
        from llm_code.tools.search_backends.tavily import TavilyBackend
        return TavilyBackend(api_key=tavily_api_key)
    elif backend_name == "searxng":
        from llm_code.tools.search_backends.searxng import SearXNGBackend
        return SearXNGBackend(base_url=searxng_base_url)
    else:
        raise ValueError(f"Unknown search backend: {backend_name}")
```

```python
# llm_code/tools/search_backends/duckduckgo.py
"""DuckDuckGo search backend using the HTML Lite interface."""

from __future__ import annotations

import re
import time

import httpx

from llm_code.tools.search_backends import SearchResult

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


class DuckDuckGoBackend:
    _last_request_time: float = 0.0
    _MIN_DELAY: float = 1.0

    @property
    def name(self) -> str:
        return "duckduckgo"

    def search(self, query: str, max_results: int = 10) -> tuple[SearchResult, ...]:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._MIN_DELAY:
            time.sleep(self._MIN_DELAY - elapsed)

        try:
            response = httpx.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "llm-code/1.0 (web-search)"},
                follow_redirects=True,
                timeout=15.0,
            )
            self.__class__._last_request_time = time.monotonic()
        except Exception:
            return ()

        if response.status_code != 200:
            return ()

        return self._parse_results(response.text, max_results)

    def _parse_results(self, html: str, max_results: int) -> tuple[SearchResult, ...]:
        results: list[SearchResult] = []
        link_pattern = re.compile(
            r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title_html) in enumerate(links):
            if i >= max_results:
                break
            title = _strip_tags(title_html)
            snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
            if url and title:
                results.append(SearchResult(title=title, url=url, snippet=snippet))

        return tuple(results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_search_backends/ -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/search_backends/ tests/test_tools/test_search_backends/
git commit -m "feat: add SearchBackend protocol + DuckDuckGo backend"
```

---

### Task 7: Tavily + SearXNG Backends

**Files:**
- Create: `llm_code/tools/search_backends/tavily.py`
- Create: `llm_code/tools/search_backends/searxng.py`
- Test: `tests/test_tools/test_search_backends/test_tavily.py`
- Test: `tests/test_tools/test_search_backends/test_searxng.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_search_backends/test_tavily.py
import pytest
from unittest.mock import patch, MagicMock
from llm_code.tools.search_backends import SearchResult
from llm_code.tools.search_backends.tavily import TavilyBackend


class TestTavilyBackend:
    def test_name(self):
        assert TavilyBackend(api_key="test-key").name == "tavily"

    def test_no_api_key_raises(self):
        with pytest.raises(ValueError, match="API key"):
            TavilyBackend(api_key="")

    @patch("llm_code.tools.search_backends.tavily.httpx")
    def test_search_success(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "Result 1", "url": "https://r1.com", "content": "Snippet 1"},
            ]
        }
        mock_httpx.post.return_value = mock_response
        results = TavilyBackend(api_key="test-key").search("test", max_results=5)
        assert len(results) == 1
        assert results[0] == SearchResult(title="Result 1", url="https://r1.com", snippet="Snippet 1")

    @patch("llm_code.tools.search_backends.tavily.httpx")
    def test_search_error(self, mock_httpx):
        mock_httpx.post.side_effect = Exception("API error")
        assert TavilyBackend(api_key="test-key").search("test") == ()
```

```python
# tests/test_tools/test_search_backends/test_searxng.py
import pytest
from unittest.mock import patch, MagicMock
from llm_code.tools.search_backends.searxng import SearXNGBackend


class TestSearXNGBackend:
    def test_name(self):
        assert SearXNGBackend(base_url="http://localhost:8888").name == "searxng"

    def test_no_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url"):
            SearXNGBackend(base_url="")

    @patch("llm_code.tools.search_backends.searxng.httpx")
    def test_search_success(self, mock_httpx):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [{"title": "R1", "url": "https://r1.com", "content": "S1"}]
        }
        mock_httpx.get.return_value = mock_response
        results = SearXNGBackend(base_url="http://localhost:8888").search("test")
        assert len(results) == 1
        assert results[0].title == "R1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_search_backends/test_tavily.py tests/test_tools/test_search_backends/test_searxng.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement Tavily + SearXNG**

```python
# llm_code/tools/search_backends/tavily.py
"""Tavily search backend: AI-optimized, requires API key."""

from __future__ import annotations

import httpx
from llm_code.tools.search_backends import SearchResult


class TavilyBackend:
    _API_URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Tavily API key is required")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "tavily"

    def search(self, query: str, max_results: int = 10) -> tuple[SearchResult, ...]:
        try:
            response = httpx.post(
                self._API_URL,
                json={"api_key": self._api_key, "query": query, "max_results": max_results},
                timeout=15.0,
            )
            if response.status_code != 200:
                return ()
            data = response.json()
        except Exception:
            return ()

        results: list[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            ))
        return tuple(results)
```

```python
# llm_code/tools/search_backends/searxng.py
"""SearXNG search backend: self-hosted, no API key needed."""

from __future__ import annotations

import httpx
from llm_code.tools.search_backends import SearchResult


class SearXNGBackend:
    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise ValueError("SearXNG base_url is required")
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "searxng"

    def search(self, query: str, max_results: int = 10) -> tuple[SearchResult, ...]:
        try:
            response = httpx.get(
                f"{self._base_url}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": "llm-code/1.0 (web-search)"},
                timeout=15.0,
            )
            if response.status_code != 200:
                return ()
            data = response.json()
        except Exception:
            return ()

        results: list[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            ))
        return tuple(results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_search_backends/ -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/search_backends/tavily.py llm_code/tools/search_backends/searxng.py tests/test_tools/test_search_backends/
git commit -m "feat: add Tavily + SearXNG search backends"
```

---

### Task 8: WebSearch Tool + Domain Filtering

**Files:**
- Create: `llm_code/tools/web_search.py`
- Test: `tests/test_tools/test_web_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_web_search.py
import pytest
from unittest.mock import patch, MagicMock
from llm_code.tools.web_search import WebSearchTool
from llm_code.tools.base import PermissionLevel
from llm_code.tools.search_backends import SearchResult
from llm_code.runtime.config import WebSearchConfig


class TestWebSearchToolProperties:
    def setup_method(self):
        self.tool = WebSearchTool(config=WebSearchConfig())

    def test_name(self):
        assert self.tool.name == "web_search"

    def test_permission(self):
        assert self.tool.required_permission == PermissionLevel.FULL_ACCESS

    def test_input_schema_has_query(self):
        assert "query" in self.tool.input_schema["properties"]
        assert "query" in self.tool.input_schema["required"]


class TestWebSearchDomainFiltering:
    def test_denylist(self):
        tool = WebSearchTool(config=WebSearchConfig(domain_denylist=("*.spam.com",)))
        results = (
            SearchResult(title="Good", url="https://good.com/page", snippet="ok"),
            SearchResult(title="Spam", url="https://ads.spam.com/page", snippet="bad"),
        )
        assert len(tool._filter_results(results)) == 1

    def test_allowlist(self):
        tool = WebSearchTool(config=WebSearchConfig(domain_allowlist=("*.python.org",)))
        results = (
            SearchResult(title="Docs", url="https://docs.python.org/3/", snippet="ok"),
            SearchResult(title="Other", url="https://other.com/", snippet="nope"),
        )
        assert len(tool._filter_results(results)) == 1

    def test_empty_lists_allow_all(self):
        tool = WebSearchTool(config=WebSearchConfig())
        results = (
            SearchResult(title="A", url="https://a.com/", snippet="a"),
            SearchResult(title="B", url="https://b.com/", snippet="b"),
        )
        assert len(tool._filter_results(results)) == 2


class TestWebSearchOutput:
    def test_format_results(self):
        tool = WebSearchTool(config=WebSearchConfig())
        results = (SearchResult(title="Docs", url="https://docs.python.org", snippet="Official"),)
        output = tool._format_results("python", results)
        assert "Docs" in output and "Search Results" in output


class TestWebSearchExecute:
    @patch("llm_code.tools.web_search.create_backend")
    def test_execute_success(self, mock_factory):
        mock_backend = MagicMock()
        mock_backend.search.return_value = (
            SearchResult(title="Result", url="https://r.com", snippet="Found"),
        )
        mock_factory.return_value = mock_backend
        result = WebSearchTool(config=WebSearchConfig()).execute({"query": "test"})
        assert result.is_error is False
        assert "Result" in result.output

    def test_execute_missing_query(self):
        result = WebSearchTool(config=WebSearchConfig()).execute({})
        assert result.is_error is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_search.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement WebSearch tool**

```python
# llm_code/tools/web_search.py
"""WebSearch tool: search the web with pluggable backends."""

from __future__ import annotations

import os
from fnmatch import fnmatch
from urllib.parse import urlparse

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.search_backends import SearchResult, create_backend
from llm_code.runtime.config import WebSearchConfig


class WebSearchInput(BaseModel):
    query: str
    max_results: int = 10
    backend: str = "auto"


class WebSearchTool(Tool):
    def __init__(self, config: WebSearchConfig | None = None) -> None:
        self._config = config or WebSearchConfig()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web and return results with titles, URLs, and snippets. "
            "Supports multiple backends: duckduckgo (default), tavily, searxng."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results", "default": 10},
                "backend": {
                    "type": "string",
                    "description": "Search backend",
                    "enum": ["auto", "duckduckgo", "tavily", "searxng"],
                    "default": "auto",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    @property
    def input_model(self) -> type[WebSearchInput]:
        return WebSearchInput

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def _resolve_backend_name(self, requested: str) -> str:
        return self._config.default_backend if requested == "auto" else requested

    def _get_backend_kwargs(self, backend_name: str) -> dict:
        kwargs: dict = {}
        if backend_name == "tavily":
            kwargs["tavily_api_key"] = os.environ.get(self._config.tavily_api_key_env, "")
        elif backend_name == "searxng":
            kwargs["searxng_base_url"] = self._config.searxng_base_url
        return kwargs

    def _filter_results(self, results: tuple[SearchResult, ...]) -> tuple[SearchResult, ...]:
        denylist = self._config.domain_denylist
        allowlist = self._config.domain_allowlist
        if not denylist and not allowlist:
            return results

        filtered: list[SearchResult] = []
        for r in results:
            try:
                domain = urlparse(r.url).hostname or ""
            except Exception:
                continue
            if any(fnmatch(domain, pat) for pat in denylist):
                continue
            if allowlist and not any(fnmatch(domain, pat) for pat in allowlist):
                continue
            filtered.append(r)
        return tuple(filtered)

    def _format_results(self, query: str, results: tuple[SearchResult, ...]) -> str:
        if not results:
            return f'No results found for "{query}"'
        lines = [f'## Search Results for "{query}"\n']
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **[{r.title}]({r.url})**")
            if r.snippet:
                lines.append(f"   {r.snippet}")
            lines.append("")
        lines.append(f"({len(results)} results)")
        return "\n".join(lines)

    def execute(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(output="Error: 'query' is required", is_error=True)

        backend_name = self._resolve_backend_name(args.get("backend", "auto"))
        max_results = args.get("max_results", self._config.max_results)

        try:
            kwargs = self._get_backend_kwargs(backend_name)
            backend = create_backend(backend_name, **kwargs)
        except ValueError as exc:
            return ToolResult(output=f"Backend error: {exc}", is_error=True)

        try:
            results = backend.search(query, max_results=max_results)
        except Exception as exc:
            return ToolResult(output=f"Search error: {exc}", is_error=True)

        results = self._filter_results(results)
        output = self._format_results(query, results)

        return ToolResult(
            output=output,
            metadata={"query": query, "backend": backend_name, "result_count": len(results)},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_search.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/web_search.py tests/test_tools/test_web_search.py
git commit -m "feat: add WebSearch tool with pluggable backends and domain filtering"
```

---

### Task 9: pyproject.toml Dependencies

**Files:**
- Modify: `pyproject.toml:38-57`

- [ ] **Step 1: Add web optional dependencies to pyproject.toml**

```toml
web = ["readability-lxml>=0.8", "html2text>=2024.2"]
web-browser = ["readability-lxml>=0.8", "html2text>=2024.2", "playwright>=1.40"]
```

- [ ] **Step 2: Install and verify**

Run: `cd /Users/adamhong/Work/qwen/llm-code && pip install -e ".[web]"`
Expected: readability-lxml and html2text installed

- [ ] **Step 3: Run all web tests**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_web_common.py tests/test_tools/test_web_fetch.py tests/test_tools/test_web_search.py tests/test_tools/test_search_backends/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add pyproject.toml
git commit -m "chore: add web + web-browser optional dependencies"
```

---

### Task 10: Per-agent Model Override (Config)

**Files:**
- Modify: `llm_code/runtime/config.py:73-76`
- Test: `tests/test_runtime/test_config_swarm.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_config_swarm.py
import pytest
from llm_code.runtime.config import SwarmConfig


class TestSwarmConfigRoleModels:
    def test_default_empty(self):
        assert SwarmConfig().role_models == {}

    def test_with_role_models(self):
        cfg = SwarmConfig(role_models={"reviewer": "gpt-4o", "coder": "qwen"})
        assert cfg.role_models["reviewer"] == "gpt-4o"

    def test_frozen(self):
        with pytest.raises(AttributeError):
            SwarmConfig().role_models = {"new": "model"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_swarm.py -v`
Expected: FAIL

- [ ] **Step 3: Add role_models to SwarmConfig**

In `llm_code/runtime/config.py`, modify SwarmConfig (around line 73):

```python
@dataclasses.dataclass(frozen=True)
class SwarmConfig:
    enabled: bool = False
    backend: str = "auto"
    max_members: int = 5
    role_models: dict[str, str] = dataclasses.field(default_factory=dict)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_swarm.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/runtime/config.py tests/test_runtime/test_config_swarm.py
git commit -m "feat: add role_models to SwarmConfig"
```

---

### Task 11: Per-agent Model (SwarmManager + Backends)

**Files:**
- Modify: `llm_code/swarm/manager.py:42-88`
- Modify: `llm_code/swarm/backend_subprocess.py:22-67`
- Modify: `llm_code/swarm/backend_tmux.py:24-63`
- Test: `tests/test_swarm/test_model_override.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/test_model_override.py
import pytest
from pathlib import Path
from llm_code.swarm.manager import SwarmManager
from llm_code.runtime.config import RuntimeConfig, SwarmConfig, ModelRoutingConfig


class TestResolveModel:
    def _make_manager(self):
        config = RuntimeConfig(
            model="global-model",
            model_aliases={"qwen": "qwen3.5-122b-int4"},
            model_routing=ModelRoutingConfig(sub_agent="routing-agent-model"),
            swarm=SwarmConfig(role_models={"reviewer": "gpt-4o", "coder": "qwen"}),
        )
        return SwarmManager(swarm_dir=Path("/tmp/test-swarm"), config=config)

    def test_explicit_model_wins(self):
        assert self._make_manager()._resolve_model("reviewer", explicit="claude-sonnet") == "claude-sonnet"

    def test_role_mapping(self):
        assert self._make_manager()._resolve_model("reviewer", explicit=None) == "gpt-4o"

    def test_role_mapping_with_alias(self):
        assert self._make_manager()._resolve_model("coder", explicit=None) == "qwen3.5-122b-int4"

    def test_fallback_to_routing(self):
        assert self._make_manager()._resolve_model("tester", explicit=None) == "routing-agent-model"

    def test_fallback_to_global(self):
        config = RuntimeConfig(
            model="global-model",
            model_routing=ModelRoutingConfig(sub_agent=""),
            swarm=SwarmConfig(role_models={}),
        )
        mgr = SwarmManager(swarm_dir=Path("/tmp/test"), config=config)
        assert mgr._resolve_model("any", explicit=None) == "global-model"

    def test_explicit_alias_resolved(self):
        assert self._make_manager()._resolve_model("any", explicit="qwen") == "qwen3.5-122b-int4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_model_override.py -v`
Expected: FAIL

- [ ] **Step 3: Implement _resolve_model + update backends**

Add to `SwarmManager.__init__`: `config: RuntimeConfig | None = None` parameter.

Add method:

```python
def _resolve_model(self, role: str, explicit: str | None) -> str:
    model = ""
    if explicit:
        model = explicit
    elif role in self._config.swarm.role_models:
        model = self._config.swarm.role_models[role]
    elif self._config.model_routing.sub_agent:
        model = self._config.model_routing.sub_agent
    else:
        model = self._config.model
    return self._config.model_aliases.get(model, model)
```

Update `create_member` to accept `model: str | None = None` and call `_resolve_model`.

Update both backends' `spawn()` to accept `model: str = ""` and pass `--model {model}` to the command.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_model_override.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run all swarm tests for regressions**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/swarm/manager.py llm_code/swarm/backend_subprocess.py llm_code/swarm/backend_tmux.py tests/test_swarm/test_model_override.py
git commit -m "feat: add per-agent model override with 4-level fallback chain"
```

---

### Task 12: swarm_create Tool Model Parameter

**Files:**
- Modify: `llm_code/tools/swarm_create.py:12-15` and `:35-55`
- Test: `tests/test_tools/test_swarm_create_model.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_swarm_create_model.py
import pytest
from llm_code.tools.swarm_create import SwarmCreateTool, SwarmCreateInput


class TestSwarmCreateModelParam:
    def test_input_has_model(self):
        inp = SwarmCreateInput(role="coder", task="fix bug", model="gpt-4o")
        assert inp.model == "gpt-4o"

    def test_default_none(self):
        assert SwarmCreateInput(role="coder", task="fix bug").model is None

    def test_schema_has_model(self):
        schema = SwarmCreateTool().input_schema
        assert "model" in schema["properties"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_swarm_create_model.py -v`
Expected: FAIL

- [ ] **Step 3: Add model to SwarmCreateInput + input_schema + execute**

In `llm_code/tools/swarm_create.py`:

Add `model: str | None = None` to `SwarmCreateInput`.
Add `"model"` property to `input_schema`.
Pass `model=args.get("model")` in `execute()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_tools/test_swarm_create_model.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/tools/swarm_create.py tests/test_tools/test_swarm_create_model.py
git commit -m "feat: add model parameter to swarm_create tool"
```

---

### Phase 1 Checkpoint

- [ ] **Run full test suite**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest -v --tb=short`
Expected: All existing + ~60 new tests PASS

---

## Phase 2: Core Architecture Changes

---

### Task 13: Plan Data Structures

**Files:**
- Create: `llm_code/runtime/plan.py`
- Test: `tests/test_runtime/test_plan.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_plan.py
import pytest
from llm_code.runtime.plan import PlanEntry, PlanSummary, summarize_tool_call


class TestSummarizeToolCall:
    def test_edit_file(self):
        s = summarize_tool_call("edit_file", {"file_path": "src/auth.py", "old_string": "def validate_token(tok):", "new_string": "def validate_token(token: str) -> bool:"})
        assert "src/auth.py" in s and "Edit" in s

    def test_write_file(self):
        s = summarize_tool_call("write_file", {"file_path": "tests/test_auth.py", "content": "x" * 200})
        assert "tests/test_auth.py" in s and "200" in s

    def test_bash(self):
        s = summarize_tool_call("bash", {"command": "pytest tests/test_auth.py -v"})
        assert "pytest" in s

    def test_generic(self):
        s = summarize_tool_call("notebook_edit", {"path": "nb.ipynb", "cell_index": 3})
        assert "notebook_edit" in s


class TestPlanEntry:
    def test_frozen(self):
        e = PlanEntry(tool_name="bash", args={"command": "ls"}, summary="List files")
        with pytest.raises(AttributeError):
            e.tool_name = "other"


class TestPlanSummary:
    def test_render(self):
        plan = PlanSummary(entries=(
            PlanEntry(tool_name="edit_file", args={}, summary="Edit src/auth.py"),
            PlanEntry(tool_name="bash", args={}, summary="Run: pytest"),
        ))
        r = plan.render()
        assert "1." in r and "2." in r and "2 operations" in r

    def test_render_empty(self):
        assert "No operations" in PlanSummary(entries=()).render()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_plan.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement plan.py**

```python
# llm_code/runtime/plan.py
"""Plan mode data structures for presenting tool operations before execution."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PlanEntry:
    tool_name: str
    args: dict
    summary: str


@dataclasses.dataclass(frozen=True)
class PlanSummary:
    entries: tuple[PlanEntry, ...]

    def render(self) -> str:
        if not self.entries:
            return "No operations in plan."
        lines = [f"Plan ({len(self.entries)} operations)\n"]
        for i, entry in enumerate(self.entries, 1):
            lines.append(f"  {i}. [{entry.tool_name}] {entry.summary}")
        return "\n".join(lines)


def summarize_tool_call(name: str, args: dict) -> str:
    if name == "edit_file":
        path = args.get("file_path", "?")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        old_p = old[:40] + "..." if len(old) > 40 else old
        new_p = new[:40] + "..." if len(new) > 40 else new
        return f"Edit {path}: '{old_p}' -> '{new_p}'"

    if name == "write_file":
        path = args.get("file_path", "?")
        content = args.get("content", "")
        return f"Create {path} ({len(content)} chars)"

    if name == "bash":
        cmd = args.get("command", "?")
        preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return f"Run: {preview}"

    params = ", ".join(f"{k}={repr(v)[:30]}" for k, v in list(args.items())[:3])
    return f"{name}({params})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_plan.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/runtime/plan.py tests/test_runtime/test_plan.py
git commit -m "feat: add plan data structures for plan mode"
```

---

### Task 14: PermissionMode.PLAN + PermissionOutcome.NEED_PLAN

**Files:**
- Modify: `llm_code/runtime/permissions.py:13-18`, `:21-24`, `:125-177`
- Test: `tests/test_runtime/test_permissions_plan.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_permissions_plan.py
import pytest
from llm_code.tools.base import PermissionLevel
from llm_code.runtime.permissions import PermissionMode, PermissionOutcome, PermissionPolicy


class TestPlanMode:
    def test_plan_mode_exists(self):
        assert PermissionMode.PLAN.value == "plan"

    def test_need_plan_outcome_exists(self):
        assert PermissionOutcome.NEED_PLAN.value == "need_plan"

    def test_allows_read_only(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_needs_plan_for_write(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.NEED_PLAN

    def test_needs_plan_for_full_access(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.NEED_PLAN

    def test_deny_list_overrides(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN, deny_tools=frozenset({"dangerous"}))
        assert policy.authorize("dangerous", PermissionLevel.FULL_ACCESS) == PermissionOutcome.DENY

    def test_allow_list_overrides(self):
        policy = PermissionPolicy(mode=PermissionMode.PLAN, allow_tools=frozenset({"bash"}))
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.ALLOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_permissions_plan.py -v`
Expected: FAIL

- [ ] **Step 3: Add PLAN mode to permissions.py**

1. Add `PLAN = "plan"` to `PermissionMode` enum (line ~18)
2. Add `NEED_PLAN = "need_plan"` to `PermissionOutcome` enum (line ~24)
3. Add `PermissionMode.PLAN: PermissionLevel.FULL_ACCESS` to `_MODE_MAX_LEVEL`
4. In `authorize()`, add PLAN branch after deny/allow checks:

```python
if self._mode == PermissionMode.PLAN:
    if required == PermissionLevel.READ_ONLY:
        return PermissionOutcome.ALLOW
    return PermissionOutcome.NEED_PLAN
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_permissions_plan.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run existing permission tests**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/ -k permission -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/runtime/permissions.py tests/test_runtime/test_permissions_plan.py
git commit -m "feat: add PermissionMode.PLAN with NEED_PLAN outcome"
```

---

### Task 15: WorktreeConfig + SwarmConfig Update

**Files:**
- Modify: `llm_code/runtime/config.py:73-76`
- Test: `tests/test_runtime/test_config_worktree.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_config_worktree.py
import pytest
from llm_code.runtime.config import WorktreeConfig, SwarmConfig


class TestWorktreeConfig:
    def test_defaults(self):
        cfg = WorktreeConfig()
        assert cfg.on_complete == "diff"
        assert cfg.base_dir == ""
        assert cfg.copy_gitignored == (".env", ".env.local")
        assert cfg.cleanup_on_success is True

    def test_frozen(self):
        with pytest.raises(AttributeError):
            WorktreeConfig().on_complete = "merge"


class TestSwarmConfigWorktree:
    def test_has_worktree(self):
        assert isinstance(SwarmConfig().worktree, WorktreeConfig)

    def test_backend_worktree(self):
        assert SwarmConfig(backend="worktree").backend == "worktree"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_worktree.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Add WorktreeConfig**

Add before SwarmConfig in `llm_code/runtime/config.py`:

```python
@dataclasses.dataclass(frozen=True)
class WorktreeConfig:
    on_complete: str = "diff"
    base_dir: str = ""
    copy_gitignored: tuple[str, ...] = (".env", ".env.local")
    cleanup_on_success: bool = True
```

Add to SwarmConfig:

```python
    worktree: WorktreeConfig = dataclasses.field(default_factory=WorktreeConfig)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_runtime/test_config_worktree.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/runtime/config.py tests/test_runtime/test_config_worktree.py
git commit -m "feat: add WorktreeConfig to SwarmConfig"
```

---

### Task 16: Git Worktree Backend

**Files:**
- Create: `llm_code/swarm/backend_worktree.py`
- Test: `tests/test_swarm/test_backend_worktree.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/test_backend_worktree.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from llm_code.swarm.backend_worktree import WorktreeBackend, WorktreeResult
from llm_code.runtime.config import WorktreeConfig


class TestWorktreeResult:
    def test_frozen(self):
        r = WorktreeResult(member_id="abc", status="success", diff="+ line")
        with pytest.raises(AttributeError):
            r.status = "error"

    def test_defaults(self):
        r = WorktreeResult(member_id="abc", status="empty")
        assert r.diff == "" and r.branch_name == "" and r.conflict_files == ()


class TestWorktreeBackendSpawn:
    @patch("llm_code.swarm.backend_worktree.asyncio")
    @patch("llm_code.swarm.backend_worktree.subprocess")
    async def test_spawn_creates_worktree(self, mock_subprocess, mock_asyncio):
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_asyncio.create_subprocess_exec.return_value = mock_proc
        backend = WorktreeBackend(project_dir=Path("/tmp/test"), config=WorktreeConfig())
        pid = await backend.spawn("abc12345", role="coder", task="fix bug", model="qwen")
        assert pid == 12345


class TestWorktreeBackendComplete:
    async def test_complete_unknown_member(self):
        backend = WorktreeBackend(project_dir=Path("/tmp/test"), config=WorktreeConfig())
        result = await backend.complete("nonexistent")
        assert result.status == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_backend_worktree.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement WorktreeBackend**

```python
# llm_code/swarm/backend_worktree.py
"""Git worktree backend for swarm agents: full filesystem isolation."""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import subprocess
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class WorktreeResult:
    member_id: str
    status: str  # "success" | "conflict" | "empty" | "error"
    diff: str = ""
    branch_name: str = ""
    conflict_files: tuple[str, ...] = ()
    message: str = ""


@dataclasses.dataclass
class _WorktreeMember:
    member_id: str
    worktree_path: Path
    branch_name: str
    proc: asyncio.subprocess.Process | None = None


class WorktreeBackend:
    def __init__(self, project_dir: Path, config) -> None:
        from llm_code.runtime.config import WorktreeConfig
        self._project_dir = Path(project_dir)
        self._config: WorktreeConfig = config
        self._members: dict[str, _WorktreeMember] = {}

    def _worktree_path(self, member_id: str) -> Path:
        base = Path(self._config.base_dir) if self._config.base_dir else Path("/tmp")
        return base / f"llm-code-wt-{member_id}"

    def _branch_name(self, member_id: str) -> str:
        return f"agent/{member_id}"

    async def spawn(
        self, member_id: str, role: str, task: str, model: str = "", extra_args: tuple[str, ...] = (),
    ) -> int | None:
        wt_path = self._worktree_path(member_id)
        branch = self._branch_name(member_id)

        result = subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch],
            cwd=str(self._project_dir), capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None

        for filename in self._config.copy_gitignored:
            src = self._project_dir / filename
            if src.exists():
                dst = wt_path / filename
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))

        cmd_parts = ["llm-code", "--lite"]
        if model:
            cmd_parts.extend(["--model", model])
        cmd_parts.extend(extra_args)

        prompt = f"You are a {role} agent. Your task:\n{task}"
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(wt_path),
        )
        if proc.stdin:
            proc.stdin.write(prompt.encode())
            proc.stdin.close()

        self._members[member_id] = _WorktreeMember(
            member_id=member_id, worktree_path=wt_path, branch_name=branch, proc=proc,
        )
        return proc.pid

    async def stop(self, member_id: str) -> None:
        member = self._members.get(member_id)
        if member and member.proc:
            try:
                member.proc.terminate()
                await asyncio.wait_for(member.proc.wait(), timeout=10.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                if member.proc.returncode is None:
                    member.proc.kill()

    async def complete(self, member_id: str) -> WorktreeResult:
        member = self._members.get(member_id)
        if member is None:
            return WorktreeResult(member_id=member_id, status="error", message=f"Unknown member: {member_id}")

        wt_path = member.worktree_path
        branch = member.branch_name

        subprocess.run(["git", "add", "-A"], cwd=str(wt_path), capture_output=True)
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"agent/{member_id}: task complete"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        if commit_result.returncode != 0 and "nothing to commit" in commit_result.stdout:
            self._cleanup_worktree(wt_path, branch)
            self._members.pop(member_id, None)
            return WorktreeResult(member_id=member_id, status="empty", message="No changes made")

        mode = self._config.on_complete
        if mode == "diff":
            return self._complete_diff(member_id, wt_path, branch)
        elif mode == "merge":
            return self._complete_merge(member_id, wt_path, branch)
        elif mode == "branch":
            return self._complete_branch(member_id, wt_path, branch)
        return WorktreeResult(member_id=member_id, status="error", message=f"Unknown mode: {mode}")

    def _complete_diff(self, member_id: str, wt_path: Path, branch: str) -> WorktreeResult:
        diff_result = subprocess.run(
            ["git", "diff", f"HEAD...{branch}"], cwd=str(self._project_dir), capture_output=True, text=True,
        )
        if self._config.cleanup_on_success:
            self._cleanup_worktree(wt_path, branch)
        self._members.pop(member_id, None)
        return WorktreeResult(member_id=member_id, status="success", diff=diff_result.stdout, branch_name=branch)

    def _complete_merge(self, member_id: str, wt_path: Path, branch: str) -> WorktreeResult:
        merge_result = subprocess.run(
            ["git", "merge", branch], cwd=str(self._project_dir), capture_output=True, text=True,
        )
        if merge_result.returncode != 0:
            conflict_result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=str(self._project_dir), capture_output=True, text=True,
            )
            conflict_files = tuple(f.strip() for f in conflict_result.stdout.splitlines() if f.strip())
            subprocess.run(["git", "merge", "--abort"], cwd=str(self._project_dir), capture_output=True)
            return WorktreeResult(
                member_id=member_id, status="conflict", branch_name=branch,
                conflict_files=conflict_files, message=f"Conflict in {len(conflict_files)} files",
            )
        if self._config.cleanup_on_success:
            self._cleanup_worktree(wt_path, branch)
        self._members.pop(member_id, None)
        return WorktreeResult(member_id=member_id, status="success", branch_name=branch, message="Merged")

    def _complete_branch(self, member_id: str, wt_path: Path, branch: str) -> WorktreeResult:
        subprocess.run(
            ["git", "worktree", "remove", str(wt_path), "--force"],
            cwd=str(self._project_dir), capture_output=True,
        )
        self._members.pop(member_id, None)
        return WorktreeResult(member_id=member_id, status="success", branch_name=branch, message=f"Branch {branch} preserved")

    def _cleanup_worktree(self, wt_path: Path, branch: str) -> None:
        subprocess.run(["git", "worktree", "remove", str(wt_path), "--force"], cwd=str(self._project_dir), capture_output=True)
        subprocess.run(["git", "branch", "-d", branch], cwd=str(self._project_dir), capture_output=True)

    async def stop_all(self) -> None:
        for member_id in list(self._members):
            await self.stop(member_id)

    def is_running(self, member_id: str) -> bool:
        member = self._members.get(member_id)
        return member is not None and member.proc is not None and member.proc.returncode is None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_backend_worktree.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/swarm/backend_worktree.py tests/test_swarm/test_backend_worktree.py
git commit -m "feat: add git worktree backend for swarm agents"
```

---

### Task 17: SwarmManager Worktree Integration

**Files:**
- Modify: `llm_code/swarm/manager.py:117-129`
- Test: `tests/test_swarm/test_manager_worktree.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/test_manager_worktree.py
import pytest
from pathlib import Path
from unittest.mock import patch
from llm_code.swarm.manager import SwarmManager
from llm_code.runtime.config import RuntimeConfig, SwarmConfig, WorktreeConfig


class TestResolveBackendWorktree:
    def _make_manager(self, backend="auto"):
        config = RuntimeConfig(swarm=SwarmConfig(backend=backend, worktree=WorktreeConfig()))
        return SwarmManager(swarm_dir=Path("/tmp/test-swarm"), config=config)

    @patch.object(SwarmManager, "_is_git_repo", return_value=True)
    @patch.object(SwarmManager, "_git_supports_worktree", return_value=True)
    def test_auto_prefers_worktree(self, *_):
        assert self._make_manager()._resolve_backend("auto") == "worktree"

    @patch.object(SwarmManager, "_is_git_repo", return_value=False)
    def test_auto_fallback_no_git(self, _):
        assert self._make_manager()._resolve_backend("auto") in ("tmux", "subprocess")

    def test_explicit_worktree(self):
        assert self._make_manager()._resolve_backend("worktree") == "worktree"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_manager_worktree.py -v`
Expected: FAIL

- [ ] **Step 3: Add worktree to _resolve_backend + helper methods**

Update `_resolve_backend` in manager.py to check worktree first in auto mode. Add `_is_git_repo()` and `_git_supports_worktree()` helper methods. Add worktree backend case in `create_member`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/test_manager_worktree.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run all swarm tests**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest tests/test_swarm/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add llm_code/swarm/manager.py tests/test_swarm/test_manager_worktree.py
git commit -m "feat: integrate worktree backend into SwarmManager"
```

---

### Task 18: Tool Registration

**Files:**
- Modify: tool registration site (find with grep)

- [ ] **Step 1: Find registration site**

Run: `grep -rn "registry.register" llm_code/ --include="*.py" | head -20`

- [ ] **Step 2: Register WebFetch + WebSearch**

```python
from llm_code.tools.web_fetch import WebFetchTool
from llm_code.tools.web_search import WebSearchTool

registry.register(WebFetchTool(config=runtime_config.web_fetch))
registry.register(WebSearchTool(config=runtime_config.web_search))
```

- [ ] **Step 3: Verify imports work**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -c "from llm_code.tools.web_fetch import WebFetchTool; from llm_code.tools.web_search import WebSearchTool; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /Users/adamhong/Work/qwen/llm-code
git add -A
git commit -m "feat: register WebFetch + WebSearch tools"
```

---

## Final Verification

- [ ] **Run complete test suite**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -m pytest -v --tb=short 2>&1 | tail -20`
Expected: 2,400+ tests PASS

- [ ] **Verify all imports**

Run: `cd /Users/adamhong/Work/qwen/llm-code && python -c "from llm_code.tools.web_fetch import WebFetchTool; from llm_code.tools.web_search import WebSearchTool; from llm_code.swarm.backend_worktree import WorktreeBackend; from llm_code.runtime.plan import PlanSummary; from llm_code.runtime.permissions import PermissionMode; print(f'All OK. PLAN={PermissionMode.PLAN.value}')"`
Expected: `All OK. PLAN=plan`

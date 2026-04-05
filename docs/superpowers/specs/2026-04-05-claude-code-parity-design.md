# LLM-Code: Claude Code Parity Features Design Spec

**Date:** 2026-04-05
**Author:** Adam
**Status:** Approved
**Scope:** 5 high-priority features to close the gap with Claude Code

---

## Overview

### Decision Summary

| Feature | Decision |
|---------|----------|
| WebSearch backend | Pluggable architecture, default DuckDuckGo |
| WebFetch content | Readability + html2text, optional playwright |
| Worktree lifecycle | Configurable, default diff |
| Plan mode | New PermissionMode.PLAN |
| Per-agent model | Tool param > role mapping > routing > global |

### Implementation Phases

```
Phase 1 (independent, parallelizable)
  ├── Feature 1: WebFetch tool
  ├── Feature 2: WebSearch tool
  └── Feature 3: Per-agent model override

Phase 2 (core architecture changes)
  ├── Feature 4: Plan mode
  └── Feature 5: Git worktree backend
```

---

## Feature 1: WebFetch Tool

### Files

```
llm_code/tools/web_fetch.py        — WebFetch tool
llm_code/tools/web_common.py       — Shared: URL safety, cache, content extraction
```

### Tool Interface

```python
class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its content as markdown."
    required_permission = PermissionLevel.FULL_ACCESS
```

Input schema:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| url | str | required | Target URL |
| prompt | str | optional | Focus hint (metadata, no LLM call) |
| max_length | int | 50000 | Truncation limit (chars) |
| raw | bool | False | Skip readability extraction |
| renderer | str | "auto" | "auto" / "default" / "browser" |

### Content Processing Pipeline (Dual Track)

```
URL -> resolve renderer
       |
       +-- renderer = "default"
       |     httpx.get (follow redirects, 30s timeout)
       |     Content-Type:
       |       text/html    -> readability-lxml -> html2text
       |       application/json -> json.dumps(indent=2)
       |       text/*        -> plain text
       |
       +-- renderer = "browser" (config or param)
             playwright chromium.launch(headless=True)
             page.goto(url, wait_until="networkidle", timeout=30s)
             page.content()
             readability-lxml -> html2text
             browser.close()
       |
       -> truncate to max_length
       -> ToolResult(output=content, metadata={url, status_code, content_type})
```

### URL Safety Classification

```python
@dataclass(frozen=True)
class UrlSafetyResult:
    classification: str   # "safe" | "needs_confirm" | "blocked"
    reasons: tuple[str, ...]
```

Rules:

| Classification | Patterns |
|---------------|----------|
| blocked | `file://`, private IPs (10.x, 172.16-31.x, 192.168.x, 127.x, ::1), cloud metadata (169.254.169.254) |
| needs_confirm | localhost, non-standard ports, IP-only URLs |
| safe | Standard HTTP/HTTPS URLs |

### Cache

```python
@dataclass(frozen=True)
class CacheEntry:
    content: str
    fetched_at: float
    ttl: float = 900.0  # 15 minutes

class UrlCache:
    """In-memory LRU, max 50 entries, 15min TTL."""
```

### Config

```python
@dataclass(frozen=True)
class WebFetchConfig:
    default_renderer: str = "default"   # "default" | "browser"
    browser_timeout: float = 30.0
    cache_ttl: float = 900.0
    cache_max_entries: int = 50
    max_length: int = 50000
```

### Renderer Resolution

When `renderer="auto"`:
- Use `config.web_fetch.default_renderer` value ("default" or "browser")
- If resolved to "browser" but playwright not installed, fallback to "default" with warning

When `renderer="browser"` explicitly:
- Attempt playwright import
- If not installed, fallback to "default" with warning in ToolResult output
- Warning format: `"[warn] playwright not installed, using default renderer. Install: pip install llm-code[web-browser] && playwright install chromium"`

### Dependencies

```toml
[project.optional-dependencies]
web = ["readability-lxml>=0.8", "html2text>=2024.2"]
web-browser = ["readability-lxml>=0.8", "html2text>=2024.2", "playwright>=1.40"]
```

---

## Feature 2: WebSearch Tool

### Files

```
llm_code/tools/web_search.py
llm_code/tools/search_backends/
    __init__.py                  — SearchBackend protocol + factory
    duckduckgo.py                — Default, zero-cost
    tavily.py                    — AI-optimized, needs API key
    searxng.py                   — Self-hosted
```

### SearchBackend Protocol

```python
@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str

class SearchBackend(Protocol):
    def search(self, query: str, max_results: int = 10) -> tuple[SearchResult, ...]: ...

    @property
    def name(self) -> str: ...
```

### Backend Implementations

| Backend | Method | Dependencies | Rate Limit |
|---------|--------|-------------|------------|
| DuckDuckGo | httpx + HTML parse (lite.duckduckgo.com) | None (httpx is core) | 1s delay between requests |
| Tavily | POST api.tavily.com/search | None (httpx) | API key required |
| SearXNG | GET {base_url}/search?format=json | None (httpx) | Self-hosted |

### Tool Interface

```python
class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web and return results with titles, URLs, and snippets."
    required_permission = PermissionLevel.FULL_ACCESS
```

Input schema:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| query | str | required | Search query |
| max_results | int | 10 | Max results to return |
| backend | str | "auto" | "auto" / "duckduckgo" / "tavily" / "searxng" |

### Config

```python
@dataclass(frozen=True)
class WebSearchConfig:
    default_backend: str = "duckduckgo"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    searxng_base_url: str = ""
    max_results: int = 10
    domain_allowlist: tuple[str, ...] = ()
    domain_denylist: tuple[str, ...] = ()
```

### Output Format

```markdown
## Search Results for "python asyncio tutorial"

1. **[Real Python: Async IO in Python](https://realpython.com/async-io-python/)**
   A complete walkthrough of Python's asyncio module...

2. **[Python Docs: asyncio](https://docs.python.org/3/library/asyncio.html)**
   Official documentation for the asyncio library...

(10 results)
```

### Domain Filtering

Precedence: denylist (fnmatch) > allowlist (fnmatch) > allow all.

---

## Feature 3: Per-agent Model Override

### Files Changed

```
llm_code/runtime/config.py           — SwarmConfig.role_models
llm_code/swarm/manager.py            — create_member + _resolve_model
llm_code/swarm/backend_subprocess.py  — spawn passes --model
llm_code/swarm/backend_tmux.py       — spawn passes --model
llm_code/tools/swarm_create.py       — input_schema adds model field
```

### Fallback Chain

```
explicit tool param model
    | (empty)
config.swarm.role_models[role]
    | (no match)
config.model_routing.agent
    | (empty)
config.model
```

Model aliases resolved at each level via `config.model_aliases`.

### Config Change

```python
@dataclass(frozen=True)
class SwarmConfig:
    max_members: int = 5
    backend_preference: str = "auto"
    role_models: dict[str, str] = field(default_factory=dict)
```

Example config.json:

```json
{
  "swarm": {
    "role_models": {
      "reviewer": "gpt-4o",
      "researcher": "claude-sonnet",
      "coder": "qwen"
    }
  }
}
```

### SwarmManager Changes

```python
async def create_member(
    self,
    role: str,
    task: str,
    model: str | None = None,
    backend: str = "auto",
) -> SwarmMember: ...

def _resolve_model(self, role: str, explicit: str | None) -> str:
    """4-level fallback chain with alias resolution."""
```

### SwarmMember Update

```python
@dataclass(frozen=True)
class SwarmMember:
    id: str
    role: str
    task: str
    model: str          # actual resolved model
    backend: str
    pid: int | None = None
    status: str = "running"
```

### Backend Changes

Both backends pass `--model {effective_model}` to the spawned llm-code process.

---

## Feature 4: Plan Mode (PermissionMode.PLAN)

### Files Changed

```
llm_code/runtime/permissions.py    — PLAN mode + NEED_PLAN outcome
llm_code/runtime/conversation.py   — intercept write tools, present plan
llm_code/tui/app.py               — Shift+Tab cycle includes PLAN
llm_code/tui/chat_widgets.py      — PlanBlock widget
```

### Permission Changes

```python
class PermissionMode(Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"
    PROMPT = "prompt"
    AUTO_ACCEPT = "auto_accept"
    PLAN = "plan"

class PermissionOutcome(Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_PROMPT = "need_prompt"
    NEED_PLAN = "need_plan"
```

PLAN mode authorize logic:
- `tool.is_read_only(args) == True` -> ALLOW
- Otherwise -> NEED_PLAN

### Conversation Loop Behavior

When NEED_PLAN is returned, the conversation loop:

1. Collects all write tool calls from the current turn
2. Generates a PlanSummary with one-line descriptions per operation
3. Presents the plan to the user with options:
   - `[y]` Execute all
   - `[n]` Cancel (returns "User cancelled plan" to LLM)
   - `[e]` Edit plan (remove individual steps)
   - `[s]` Step-by-step (confirm each tool call individually)

### Data Structures

```python
@dataclass(frozen=True)
class PlanEntry:
    tool_name: str
    args: dict
    summary: str

@dataclass(frozen=True)
class PlanSummary:
    entries: tuple[PlanEntry, ...]
    def render(self) -> str: ...
```

### Summary Generation

```python
def _summarize_tool_call(name: str, args: dict) -> str:
    # edit_file:  "Edit {path}: replace '{old[:40]}...' -> '{new[:40]}...'"
    # write_file: "Create {path} ({len(content)} chars)"
    # bash:       "Run: {command[:60]}"
    # other:      "{tool_name}({key=val, ...})"
```

### Write Tool Detection

```python
def _is_write_tool(tool: Tool, args: dict) -> bool:
    return not tool.is_read_only(args)
```

### TUI Integration

Mode cycle: `prompt -> plan -> auto_accept -> read_only -> prompt`

Status bar displays: `[PLAN] model: qwen3.5-122b | tokens: 12.3k/128k`

---

## Feature 5: Git Worktree Backend

### Files

```
llm_code/swarm/backend_worktree.py   — WorktreeBackend
llm_code/swarm/manager.py            — worktree backend selection
llm_code/runtime/config.py           — WorktreeConfig
```

### Config

```python
@dataclass(frozen=True)
class WorktreeConfig:
    on_complete: str = "diff"              # "diff" | "merge" | "branch"
    base_dir: str = ""                     # empty = /tmp/llm-code-wt-{id}
    copy_gitignored: tuple[str, ...] = (".env", ".env.local")
    cleanup_on_success: bool = True

@dataclass(frozen=True)
class SwarmConfig:
    max_members: int = 5
    backend_preference: str = "auto"       # "auto" | "tmux" | "subprocess" | "worktree"
    role_models: dict[str, str] = field(default_factory=dict)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
```

### Backend Interface

```python
class WorktreeBackend:
    def __init__(self, project_dir: Path, config: WorktreeConfig) -> None: ...

    async def spawn(
        self,
        member_id: str,
        role: str,
        task: str,
        model: str = "",
        extra_args: tuple[str, ...] = (),
    ) -> int | None: ...

    async def stop(self, member_id: str) -> None: ...
    async def complete(self, member_id: str) -> WorktreeResult: ...
    async def stop_all(self) -> None: ...
    def is_running(self, member_id: str) -> bool: ...
```

### Worktree Lifecycle

**spawn:**
```
git worktree add /tmp/llm-code-wt-{id} -b agent/{id}
cp configured gitignored files -> worktree
cd worktree && llm-code --lite --model {model} "{task}"
```

**complete (on_complete="diff"):**
```
git -C {worktree} add -A && commit
output = git diff main...agent/{id}
git worktree remove {path}
git branch -d agent/{id}
-> return diff text
```

**complete (on_complete="merge"):**
```
git -C {worktree} add -A && commit
git merge agent/{id}
if conflict: return conflict info, keep worktree
else: cleanup worktree + branch, return merge result
```

**complete (on_complete="branch"):**
```
git -C {worktree} add -A && commit
git worktree remove {path}
-> return branch name "agent/{id}", keep branch
```

### WorktreeResult

```python
@dataclass(frozen=True)
class WorktreeResult:
    member_id: str
    status: str                      # "success" | "conflict" | "empty" | "error"
    diff: str = ""
    branch_name: str = ""
    conflict_files: tuple[str, ...] = ()
    message: str = ""
```

### Manager Integration

Auto-detection priority: `worktree > tmux > subprocess`

Worktree requires: inside a git repo + git version >= 2.15.

### Safety

- Worktrees in /tmp/ or user-specified directory
- Branch naming: `agent/{member_id}` for easy identification
- Check uncommitted changes before `git worktree remove`
- Conflicts preserve worktree for manual resolution
- `cleanup_on_success: true` prevents worktree accumulation

---

## Cross-Cutting: Dependencies

### pyproject.toml Changes

```toml
[project.optional-dependencies]
web = ["readability-lxml>=0.8", "html2text>=2024.2"]
web-browser = ["readability-lxml>=0.8", "html2text>=2024.2", "playwright>=1.40"]
```

All other features require no new dependencies (httpx and git are already available).

### RuntimeConfig Changes

```python
@dataclass(frozen=True)
class RuntimeConfig:
    # ... existing fields ...
    web_fetch: WebFetchConfig = field(default_factory=WebFetchConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    # swarm already exists, gains role_models + worktree sub-config
```

---

## Testing Strategy

| Feature | Test Focus | Test Count Estimate |
|---------|-----------|-------------------|
| WebFetch | URL safety, cache, content extraction, renderer fallback | ~25 |
| WebSearch | Backend protocol, each backend, domain filtering, output format | ~20 |
| Per-agent model | Fallback chain, alias resolution, backend passing | ~10 |
| Plan mode | Permission outcome, plan collection, user choices (y/n/e/s) | ~15 |
| Worktree | Lifecycle (spawn/complete), on_complete modes, conflict handling, cleanup | ~20 |
| **Total** | | **~90** |

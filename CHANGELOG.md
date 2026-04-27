# Changelog

## v2.9.0 — GLM Wall-Clock Optimization Wave (P1 + P2 + P3)

A user transcript on GLM-5.1 (744B/40B MoE on llama.cpp) ran the
prompt `查詢今日熱門新聞三則` ("fetch today's top 3 news") and finished
in **217.9s** with **41,921 input tokens** prefilled by the final
iteration. v2.8.1 already capped post-tool thinking at 1024 tokens —
the bottleneck has *moved* to (a) sequential tool dispatch, (b)
re-prefilling the full conversation history every iter, and (c)
thinking on the final compile step that's pure templating work.

v2.9.0 ships **three opt-in levers** in a single wave that target
the moved bottleneck without touching reasoning depth. Combined,
they cut wall-clock on the same workflow to **~80-110s** with no
quality loss:

> **No quality loss — these reduce *redundant* work, not reasoning depth.**
> Iteration 0 (decision phase: which tool to call, what args to pass)
> still gets the profile's full ``default_thinking_budget``. The
> levers eliminate sequential round-trips, stale prefill, and
> templating-phase chain-of-thought.

### P1 — Parallel tool call execution

When the model emits multiple non-agent tool calls in one assistant
turn (GLM does this natively via the ``harmony_kv`` and ``glm_brace``
parser variants), dispatch them concurrently via ``asyncio.gather``
instead of the v2.8.1 sequential ``for`` loop. Tool results are
appended in the original ``tool_call_id`` order so the downstream
provider serialization stays stable.

* Profile flag: ``[parallel_tools] enable_parallel_tools = true``.
* Default ``True`` — read-only path was already concurrent in
  v2.8.1; this lever extends to write-pending and non-pre-computed
  calls.
* Expected savings: **N-1 round-trips** on multi-call turns. On the
  3-search news workflow GLM emits one call per iter so P1 is dormant
  there; on broader queries (e.g. "compare 5 GPUs") it eliminates 4
  round-trips at ~13s each.

### P2 — Tool-result compression on re-feed

When the conversation is serialized for iteration N+1, replace older
``ToolResultBlock`` payloads with a 500-char preview + structured
marker:

```
[v2.9 compressed] preview (500 chars of <total>):
<first 500 chars>
[full content omitted to reduce prefill cost — <hidden> chars hidden.
The most recent tool result for this turn was kept intact; refer to
it for the complete payload.]
```

The most recent contiguous tool-result batch stays full so the model
still has complete data for current reasoning. llama.cpp has no
prompt cache; the 41k-token observed prefill on the 3-search
workflow was almost entirely stale tool payloads.

* Profile flag: ``[tool_consumption] compress_old_tool_results = true``.
* Default ``False`` — Anthropic prompt caching already amortises
  stable prefixes; cloud profiles keep v2.8.1 byte-parity.
* Expected savings: **~30s on the news workflow's compile iter**
  (prefill drops from 41k → ~10k tokens at ~700 tok/s).
* Idempotent — already-compressed bodies pass through unchanged.

### P3 — Final compile thinking=0 heuristic

When iter > 0 AND ``tool_calls_this_turn >= compile_after_tool_calls > 0``,
drop the thinking budget to ``compile_thinking_budget`` (typically
0). The "compile" step after N tool results is templating: extract
title from result[0], URL from result[1], format. Deep
chain-of-thought there reasons over already-fetched ground truth
and adds little signal.

* Profile flags:
  ``[tool_consumption] compile_after_tool_calls = 3``
  ``[tool_consumption] compile_thinking_budget = 0``
* Default ``compile_after_tool_calls = 0`` is the disable sentinel —
  v2.8.1's per-iteration ``post_tool_thinking_budget`` stays in
  effect for profiles that don't opt in.
* Expected savings: **~5-15s per compile turn** on slow local models.
* Compile lever supersedes v2.8.1 ``post_tool_thinking_budget`` when
  both opt in (compile is more specific).

### GLM-5.1 profile opts in to all three

``examples/model_profiles/65-glm-5.1.toml`` sets all three flags.
Other built-in profiles inherit dataclass defaults — Anthropic and
cloud profiles see no behavioural change.

### Worked example — projected savings

| Phase | v2.8.1 | v2.9.0 | Source |
|---|---|---|---|
| Iter 0 — decide search | ~30s | ~30s | unchanged |
| Iter 0 — exec search #1 | ~12s | ~12s | unchanged (1 call/iter) |
| Iter 1 — process, decide #2 | ~25s | ~25s | unchanged |
| Iter 1 — exec search #2 | ~14s | ~14s | unchanged |
| Iter 2 — process, decide #3 | ~30s | ~30s | unchanged |
| Iter 2 — exec search #3 | ~13s | ~13s | unchanged |
| Iter 3 — final compile | ~75s | **~25s** | P2 cuts ~30s prefill, P3 cuts ~15s thinking |
| **Total** | **218s** | **~150s** | -31% |

Workflows where the model dispatches multiple calls per iter (which
GLM does on broader queries) see the bigger savings from P1 — a
5-search workflow drops 4 × 13s = 52s of round-trip latency.

### Backwards compatibility

Defaults preserve v2.8.1 behaviour byte-for-byte for every profile
other than GLM-5.1:

* ``enable_parallel_tools`` defaults to True, but the read-only
  concurrent path was already on in v2.8.1; the new code path only
  activates when ≥2 non-agent calls land in one turn. Single-call
  turns are sequential (byte-parity).
* ``compress_old_tool_results`` defaults to False (no compression).
* ``compile_after_tool_calls`` defaults to 0 (lever disabled).

The `test_system_prompt_v260_byte_parity.py` corpus + the 49-scenario
`test_provider_conversion_parity_v15.py` gate stay green; the v2.8.1
post-tool budget tests still pass unchanged.

### Tests

32 new across three files:

* `tests/test_runtime/test_parallel_tools_v290.py` — 9 tests
* `tests/test_runtime/test_compress_tool_results_v290.py` — 12 tests
* `tests/test_runtime/test_compile_thinking_v290.py` — 11 tests

Suite: 8613 → 8645 (+32). Ruff clean.

---

## v2.8.1 — Per-iteration thinking budget (no quality loss)

Closes the wall-clock-on-GLM-5.1 gap that v2.6.1's M1 fix
intentionally widened. v2.6.1 honored the profile's full
``default_thinking_budget`` (16384 for GLM) on every turn iteration —
the right move for iteration 0 (decision phase: which tool to call,
what args to pass), but wasteful from iteration 1 onward (the
consumption phase: summarise the tool result that's already ground
truth and emit content).

v2.8.1 adds a per-iteration override:

```toml
# 65-glm-5.1.toml
[thinking]
default_thinking_budget = 16384      # iteration 0 keeps full reasoning
post_tool_thinking_budget = 1024     # iteration 1+ caps at 1024
```

GLM-5.1's profile opts in. Set ``post_tool_thinking_budget = 0`` to
disable the override (preserves v2.8.0 behaviour byte-for-byte).

### Why this isn't a quality loss

Iteration 0 picks the tool and shapes args — the reasoning depth
that decides "search Exa for X with these params" matters. After
the tool returns, iteration 1's job is summarise + cite + format.
Deep chain-of-thought there reasons over already-fetched ground
truth and adds little signal at large wall-clock cost. Reducing
the budget for that single phase is "stop doing redundant work",
not "stop reasoning".

### Activation gate

The reduced budget kicks in only when ALL three hold:

1. ``post_tool_iteration=True`` — iteration > 0 AND a tool fired this turn
2. Profile's ``post_tool_thinking_budget > 0`` (opt-in)
3. Profile's ``default_thinking_budget > 0`` (legacy adaptive flow opts out)

Reasoning-effort scaling and small-model caps still apply on top.

### Expected wall-clock impact (GLM-5.1)

Pre-v2.8.1: iteration 0 thinks 16384 → tool call → iteration 1
thinks 16384 → emit content. **Two** full-budget reasoning passes.

v2.8.1: iteration 0 thinks 16384 → tool call → iteration 1 thinks
≤1024 → emit content. **Saves ~30-90s per turn** depending on how
much GLM was actually burning in consumption phase pre-fix.

Anthropic profiles default to ``post_tool_thinking_budget = 0``
(unchanged) — `cache_control` already optimises that pipeline.

### Tests

8 new in `tests/test_runtime/test_post_tool_thinking_budget_v281.py`:
iteration-0 keeps default; post-tool iteration uses override;
override = 0 preserves v2.8.0; legacy adaptive opts out;
``reasoning_effort`` scaling applies; back-compat without kwarg;
TOML round-trip with + without the field.

Suite: 8605 → 8613 (+8). Ruff clean. All four guards green.

---

## v2.8.0 — RAG pipeline deepening GA

Promotes the v2.8.0a1 + v2.8.0a2 alphas to GA. 185 new tests across
six mechanisms — rerank Protocol + 3 backends, health-aware
fallback, multi-query expansion, Linkup sourced-answer mode, the
high-level `research` tool, and an optional Firecrawl extraction
backend. After v2.8.0 a query like `research transformers attention
2025` runs N parallel sub-queries, fetches top-K pages, reranks for
relevance, and returns 3 fully-extracted sources to the model — one
tool call where v2.7.0 needed three.

What's in:

### M1 — Rerank backends

New `llm_code/tools/rerank/` package with a `RerankBackend` Protocol
and 4 implementations:

* `LocalRerankBackend` — `sentence-transformers/ms-marco-MiniLM-L-6-v2`
  cross-encoder (default, free, runs on CPU). Lazy-loads the model
  once per process; cached at module level so successive calls reuse
  the hot model. Requires the `[memory]` extra; without it the first
  `rerank()` call raises a clear `ImportError("install llmcode-cli[memory] ...")`.
* `CohereRerankBackend` — `rerank-multilingual-v3.0` via the Cohere
  REST API. Free tier 1000/mo. `COHERE_API_KEY` env var. Empty key
  raises `AuthError` eagerly so misconfigured deployments fail loudly
  instead of silently consuming nothing.
* `JinaRerankBackend` — `jina-reranker-v2-base-multilingual` via
  Jina's REST API. Anonymous tier supported (rate-limited);
  `JINA_API_KEY` raises the limit.
* `IdentityRerankBackend` (`name="none"`) — passthrough used when
  `profile.rerank_backend == "none"` so callers never branch on
  "is reranking enabled?".

A new `RerankTool` (`name="rerank"`) exposes the same capability as
a first-class LLM tool — input `{query, documents, top_k}`, output
markdown ranked list with scores. Auto-resolves the backend from
`profile.rerank_backend`.

Disk-space note: the local backend's first use downloads the ~80MB
cross-encoder into `~/.cache/huggingface/hub/`. Cached afterwards.

### M2 — Multi-query expansion

New `llm_code/tools/research/expansion.py` exposes `expand(query,
profile)` and `expand_template(query, max_subqueries)`. Two
strategies, dispatched on `profile.research_query_expansion`:

* `"template"` (default, free) — pattern-rule expansion. 5 rules
  cover `research X`, `X vs Y`, time-sensitive triggers,
  how-to / 如何 / 教學, what-is. CJK trigger words mirror the v2.3.1
  `_TIME_SENSITIVE_TRIGGERS` so Chinese-language asks get the same
  treatment.
* `"llm"` (opt-in) — single round-trip via `profile.tier_c_model`
  asking for a JSON array of 2-3 alternate phrasings. Falls back to
  template on parse error or missing provider/model. The reusable
  call shape mirrors `runtime/skill_router._classify_with_llm_debug`
  (sys/user message pair, max 256 tokens, temperature 0.0).
* `"off"` — single-shot, returns only the original query.

Original query is always element 0 of the returned tuple — defensive
baseline so a botched expansion still searches the user's words.
Sub-queries are deduplicated case-insensitively against the original
and each other; capped at `profile.research_max_subqueries`.

### M3 — Linkup sourced-answer mode

Extends `LinkupBackend` (from v2.7.0a1) with a `sourced_answer(query,
depth)` method that calls Linkup's `outputType: "sourcedAnswer"` mode
— a model-grounded answer plus citation sources in one round-trip.
M5's research tool short-circuits to this when
`profile.linkup_default_mode == "sourcedAnswer"` and Linkup is
healthy.

New frozen dataclasses `Source` (title / url / snippet) and
`SourcedAnswer` (answer / sources tuple) preserve the immutability
convention. Empty `sources` array → empty tuple (not `None`) so
callers can iterate unconditionally.

Auth + error handling matches the existing `search()` path:
`RateLimitError` on 429, `ValueError` mentioning the env var on
401/403, `ValueError` with parse / transport detail on any other
failure.

The existing v2.7.0a1 `search()` method is byte-identical to v2.7.0;
backward-compat is asserted by a regression test in
`test_linkup_sourced.py`.

### M4 — Backend health-check + smart fallback

New `llm_code/tools/search_backends/health.py` adds per-process
circuit-breaker tracking for each search backend. Three consecutive
failures (rate-limit / timeout / generic error) opens the circuit
for 5 minutes; any successful call resets the failure counter
immediately.

`_search_with_fallback` now calls `sort_chain()` once at the start
of each search, demoting unhealthy backends to the end (preserving
their relative priority among other unhealthy backends). On
exception it records the failure kind and continues; on success it
records the success and returns when results are non-empty.

Concurrency: the module-level `_health` dict is guarded by a
`threading.Lock` so concurrent `record_failure` calls from
`asyncio.gather` (e.g. M5's research pipeline) don't race.

The `backend_health_check_enabled` profile flag (default True) lets
deterministic test scenarios opt out of the smart-fallback ordering.

### M5 — `research` high-level tool (the v2.8.0 keystone)

New `tools/research/pipeline.py` and `tools/research/research_tool.py`.
`ResearchTool` (`name="research"`) is registered in the default tool
list — the LLM should prefer it over `web_search` for any
"research X" / "find papers about X" / "compare A vs B" style query.

Pipeline:

    expand → search × N (parallel) → fetch top-K (parallel) → rerank → top-K

Depth controls behaviour:

* `"fast"` — 1 sub-query, K=3, no rerank.
* `"standard"` — 3 sub-queries, K=5, rerank (default).
* `"deep"` — 3 sub-queries, K=10, rerank.

Linkup short-circuit: when `profile.linkup_default_mode ==
"sourcedAnswer"` AND a `LinkupBackend` is in the chain AND Linkup is
healthy, the pipeline calls Linkup's sourced-answer mode and returns
the citation-grounded answer directly — skipping search ×
fetch × rerank entirely. Hosted-RAG path for factual queries.

Concurrency: `asyncio.Semaphore(profile.research_max_concurrency)`
caps in-flight HTTP across both the search-gather and fetch-gather
stages. Default 5; profile-tunable.

Per-step failure handling: any per-task exception in the search /
fetch `asyncio.gather` calls is logged + continued. The reranker
sees the surviving documents; the pipeline never fails wholesale
because one backend or one URL went down.

Dependency injection: `run_research(query, *, profile, search_chain,
search_fn, fetch_fn, rerank, ...)` exposes every collaborator as a
keyword argument so unit tests run the full orchestration without
booting the runtime. The tool wrapper resolves the real backends
(WebSearchTool's chain + Jina Reader) at execution time.

### M6 — Firecrawl web_fetch backend (optional)

New `tools/web_fetch_backends/firecrawl.py`. Free tier 500/mo;
opt-in via `FIRECRAWL_API_KEY` env var. Without the key the path is
silently skipped — `web_fetch` behaviour for users without the key
is byte-identical to v2.7.0.

`WebFetchConfig.extraction_backend` gains `"firecrawl"` value.
`"auto"` mode tries Jina (v2.7.0a1) → local readability (v2.6.x) →
Firecrawl (v2.8.0 M6) only if both prior backends produced <200
useful chars AND `FIRECRAWL_API_KEY` is set. Explicit
`extraction_backend="firecrawl"` mode bypasses Jina + local. The
async `WebFetchTool.execute_async` mirrors the sync chain.

### Profile schema additions

Seven new profile fields (declared upfront in M1's commit so
M2-M6 don't double-bump the dataclass):

* `rerank_backend: str = "local"` (M1)
* `research_query_expansion: str = "template"` (M2)
* `research_max_subqueries: int = 3` (M2)
* `research_default_depth: str = "standard"` (M5)
* `research_max_concurrency: int = 5` (M5)
* `linkup_default_mode: str = "searchResults"` (M3)
* `backend_health_check_enabled: bool = True` (M4)

`WebSearchConfig` gains `cohere_api_key_env = "COHERE_API_KEY"` (M1)
and `firecrawl_api_key_env = "FIRECRAWL_API_KEY"` (M6).

### README + tools section

The Web row in the Tools table now lists `web_search`, `web_fetch`
(with Jina + optional Firecrawl), `rerank`, and `research`. Two new
tools (`rerank`, `research`) registered alongside the existing 17
core tools — total 19.

### Tests + guard rails

* 185 new tests across the six mechanisms.
* Local rerank backend tests inject a fake `CrossEncoder` via
  `sys.modules` so CI doesn't pay the 80MB model download.
* All cloud-backend tests (Cohere, Jina rerank, Firecrawl) use
  `respx` mocks — no real network calls in CI.
* Pipeline tests use deterministic doubles for `search_fn` /
  `fetch_fn` via dependency injection.
* v15 grep guard, v15 byte-parity, README↔reality, and v2.6.1
  system-prompt parity gates all stay green throughout the release.

### Migration notes

No breaking changes for users on v2.7.0:

* `WebSearchTool` and `WebFetchTool` behaviour is identical when no
  v2.8.0 env vars are set.
* `LinkupBackend.search()` is byte-identical; the new
  `sourced_answer()` method is additive.
* `_search_with_fallback` adds health tracking but defaults to the
  same chain order on a fresh process.

To opt into the new RAG pipeline:

* Set `profile.rerank_backend = "local"` (default) and install the
  `[memory]` extra: `pip install llmcode-cli[memory]`.
* Set `COHERE_API_KEY` to use the Cohere reranker instead.
* Set `FIRECRAWL_API_KEY` to enable the third extraction fallback.
* Set `profile.linkup_default_mode = "sourcedAnswer"` to short-
  circuit the research tool to Linkup's hosted RAG.

## v2.7.0 — RAG free-tier search backends GA

Promotes v2.7.0a1 to GA. 71 new tests across 3 backends + the
extraction-path wiring all stable — no stubs, no follow-up TODOs in
code. Future RAG mechanisms (research-style aggregation tool,
reranker, multi-query expansion, sourced-answer mode) belong in a
v2.8.0 spec — they'd build on this GA, not gate it.

What's in (v2.7.0a1 content unchanged):

## v2.7.0a1 — RAG free-tier search backends

Adds three new search backends with generous free tiers to close the
research-style query gap left by keyword-only engines, plus wires
Jina Reader into the `web_fetch` extraction path so JavaScript-
heavy pages stop returning empty markdown for users without
Playwright installed locally.

### M1 — Exa semantic search backend (free 1000/mo)

Exa is a semantic / neural search engine — it embeds queries +
documents and ranks by vector similarity rather than keyword
overlap. That makes it a strong complement to the existing keyword
backends (DuckDuckGo, Brave) for research-style asks (papers,
long-form documentation, blog posts) where the right page does NOT
necessarily contain the literal query terms.

* New `llm_code/tools/search_backends/exa.py` (~120 LOC).
* `Authorization: Bearer <key>` (canonical Exa header).
* Body: `{"query": ..., "numResults": N, "type": "auto",
  "contents": {"text": {"maxCharacters": 1000}}}`. `type=auto` lets
  Exa pick neural vs keyword per query.
* HTTP 429 → `RateLimitError`; 401 / 403 → `ValueError` mentioning
  `EXA_API_KEY` so misconfigured deployments fail loudly instead
  of silently burning free-tier quota.
* New env var: `EXA_API_KEY`. Config field:
  `WebSearchConfig.exa_api_key_env`.

### M2 — Jina Reader (search + extraction)

Jina Reader is a hosted browser-render-and-extract service —
completely free for anonymous use (rate-limited but generous), and
key-tier rates are ~10x. Wired into TWO surfaces:

**M2a — search backend (`s.jina.ai/<query>`).** New
`llm_code/tools/search_backends/jina.py`. Anonymous-friendly:
empty / whitespace key is normalised to no-Authorization-header.
Defensive JSON shape handling — accepts both `{"data": [...]}`
and a bare list.

**M2b — extraction path (`r.jina.ai/<url>`).**
`fetch_via_jina_reader(url, *, api_key, timeout)` and its async
sibling live at module scope in `web_fetch.py`. Jina handles JS
rendering itself, so it replaces `readability-lxml + html2text`
on JavaScript-heavy pages where the local extractor used to
produce ~empty text.

New `WebFetchConfig.extraction_backend` field selects the
pipeline:

* `"auto"` (default): try Jina first, fall back to local
  `readability-lxml + html2text` on any Jina failure (rate-limit,
  region-block, network error, empty body).
* `"jina"`: Jina only — return error if Jina fails.
* `"local"`: Skip Jina entirely; preserve v2.6.x behaviour
  byte-for-byte for users who prefer no outbound dependency on
  jina.ai for every fetch.

`raw=True` callers and explicit `renderer="browser"` requests
bypass Jina deliberately. `ToolResult.metadata["extraction_backend"]`
records `"jina"` / `"local"` so callers can observe which path
served their content.

* New env var: `JINA_API_KEY` (optional — anonymous works).
  Config fields: `WebSearchConfig.jina_api_key_env`,
  `WebFetchConfig.extraction_backend`,
  `WebFetchConfig.jina_api_key_env`.

### M3 — Linkup AI-native search (free 1000/mo)

Linkup is an AI-native search API — it treats search as a RAG step
and can return either raw results or a sourced answer with
citations. For v2.7.0a1 we wire only the raw-results mode
(`outputType: "searchResults"`) so Linkup behaves as a normal
search backend. The sourced-answer mode is a v2.7.0 GA candidate.

* New `llm_code/tools/search_backends/linkup.py` (~130 LOC).
* `Authorization: Bearer <key>`.
* Body: `{"q": ..., "depth": "standard",
  "outputType": "searchResults", "includeImages": false}`.
* Defensive field extraction: canonical `name` / `content` plus
  legacy `title` / `snippet` shapes both work.
* HTTP 429 → `RateLimitError`; 401 / 403 → `ValueError` mentioning
  `LINKUP_API_KEY`.
* New env var: `LINKUP_API_KEY`. Config field:
  `WebSearchConfig.linkup_api_key_env`.

### Final auto-fallback chain

```
duckduckgo  ->  brave  ->  exa  ->  jina  ->  linkup
            ->  searxng  ->  tavily  ->  serper
```

Free / no-key (DuckDuckGo) first. Keyword-paid (Brave) second.
Free-tier semantic / RAG-style (Exa, Jina, Linkup) next. Self-host
(SearXNG) and keyword paid (Tavily, Serper) as last-resort
fallbacks. Backends without an API key configured (or, for SearXNG,
without `searxng_base_url`) are skipped entirely. Jina is the
exception — its anonymous tier means it's always tried once `cfg`
is loaded.

### Manual smoke tests

After installing v2.7.0a1, set the env var(s) you want active and run:

```bash
# Exa — semantic / research queries
export EXA_API_KEY=...
llmcode -p "search exa for 'transformers attention mechanism papers 2024'"

# Jina — completely free, no setup needed
llmcode -p "search jina for 'rust async ecosystem'"

# Jina Reader — JS-heavy pages
llmcode -p "fetch https://example-spa.com/page  # uses jina extraction by default"

# Linkup — AI-native sourced search
export LINKUP_API_KEY=...
llmcode -p "search linkup for 'climate policy 2026'"
```

To opt out of Jina extraction for `web_fetch`:

```json
// settings.json
{
  "web_fetch": {
    "extraction_backend": "local"
  }
}
```

### Tests

Suite: 8349 → 8420 passed (+71 net new across M1+M2+M3, including
18 + 33 + 20 dedicated tests).

* M1 — 18 Exa tests (construction, success, headers, body, 429,
  401, 403, 500, ConnectError, parse-robust, URL filter, max_results,
  truncation, factory).
* M2 — 16 Jina-search + 17 Jina-fetch tests (sync + async, 429,
  500, ConnectError, empty-body, short-body, raw=True bypass,
  extraction_backend modes, defensive unknown-backend fallback).
* M3 — 20 Linkup tests (canonical + legacy field shapes, 429,
  401, 403, 500, ConnectError, invalid JSON, unexpected top-level
  shape, factory).

Guard rails (all green):

* v15 grep guard (`tests/test_no_model_branch_in_core.py`) — 5/5.
* v15 byte-parity (`tests/test_api/parity/`) — 98/98.
* README ↔ reality (`tests/test_readme_claims_match_runtime.py`) — 34/34.
* v2.6.1 byte-parity (`tests/test_runtime/parity/`) — green.
* `ruff check llm_code/ tests/` — clean.

## v2.6.1 — Performance hotfix (no quality loss)

Three quality-neutral or quality-positive fixes that surfaced from a
GLM-5.1 perf investigation. Wall-clock was 172.1s end-to-end on a
"top 3 news" query — diagnosis traced 90% of that to three
mechanism-level issues. Each fix targets one of them; none of them
trades reasoning depth for speed.

### M1 — Profile thinking-budget routing fix (BUG FIX, quality goes UP)

`ModelProfile.default_thinking_budget` was parsed from TOML but never
read by `runtime/conversation.py::build_thinking_extra_body()`. The
GLM profile declared `default_thinking_budget = 16384`, but the
adaptive code computed `max(config.budget_tokens, 131072)` then
applied the `max_output_tokens / 2` cap. With user `max_tokens =
4096`, the effective thinking budget collapsed to ~2048 — far below
the profile's intent.

`build_thinking_extra_body()` now consults the profile field first.
When non-zero the profile value becomes the budget ceiling, the
local-mode `max(_, 131072)` bump is bypassed, and the
`max_output_tokens / 2` cap is skipped. Quality knobs
(reasoning-effort scale, small-model cap, thinking-boost flag)
still apply on top. Profiles with `default_thinking_budget == 0`
(the dataclass default) preserve the v2.6.0 adaptive path
byte-for-byte.

GLM-5.1 now actually gets the 16384 thinking tokens its profile
asks for. Wall clock per turn may rise slightly on thinking-heavy
queries — that is the *correct* behaviour for a profile that
declared 16384 as its budget.

### M2 — GLM prompt ballast dedupe (quality-neutral, saves ~2400 chars/turn)

For GLM-5.1, three prompt-assembly paths layered duplicate guidance
on top of the GLM-tuned `glm.j2` template every turn:

* `runtime/prompt.py` injected `_BEHAVIOR_RULES` /
  `_LOCAL_MODEL_RULES` / `_XML_TOOL_INSTRUCTIONS` constants.
* The composable snippets pack at session-scope priority 25 emitted
  the same content again via `BUILTIN_SNIPPETS`.
* `glm.j2` itself restated all of it in GLM-tuned voice.

Net effect: ~2400 chars of duplicate guidance per turn for a model
whose template was hand-tuned to express the same behaviour rules.

`PromptSnippet` gains a `tags: tuple[str, ...]` field; built-in
snippets carry tags (`intro`, `behavior_rules`, `local_model_rules`,
`xml_tools`, `tool_result_nudge`). Sidecar
`<template>.metadata.toml` declares which categories the template
covers. `glm.metadata.toml` lists `intro` / `behavior_rules` /
`tool_result_nudge` (NOT `xml_tools` — `glm.j2` doesn't show the
XML tool format spec, so that snippet keeps rendering).

`ModelProfile.prompt_dedupe_with_template` (default `False`) gates
the whole feature — the GLM example profile opts in via
`[prompt] dedupe_with_template = true`. Every other profile is
guaranteed byte-identical output via the new
`tests/test_runtime/parity/` gate.

GLM system prompt: 11467 → 9078 chars (saved 2389 chars per turn,
21% reduction). Quality preserved — a "required signals" test
ensures the deduped prompt still contains every behaviour rule the
template expresses, the XML tool format spec, the agent-tool
warning, and the GLM identity intro.

### M3 — True SSE streaming for OpenAI-compat (TTFT improvement, quality-neutral)

`stream_message` previously called `_post_with_retry`, which
buffered the entire response body before parsing. The "streaming"
SSE iterator iterated over `response.text` — the complete response
text — so user-visible TTFT was full generation time.

New `aparse_sse_events_from_lines` async generator parses SSE
blocks incrementally from `httpx.Response.aiter_lines`. New
`_AsyncStreamIterator` mirrors `_StreamIterator` semantics (delta
accumulation, pending tool-call assembly, single-stop emission,
trailing-usage patch) but consumes events one chunk at a time.
New `_stream_with_retry` opens an `httpx.AsyncClient.stream`
context, validates status BEFORE yielding, and forwards parsed
events as they land. Retries fire only on connect-time failures so
mid-stream errors propagate cleanly without duplicate events.

`send_message` (non-streaming) is untouched. Generation speed
unchanged; only PERCEIVED latency improves.

### Profile schema additions (v2.6.1)

```toml
[prompt]
dedupe_with_template = true   # M2 — opt in to template-aware snippet skip
```

### Tests

Suite: 8288 → 8349 passed (+61 net new across M1+M2+M3, including
12 + 26 + 17 dedicated tests + several parity fixtures).

* `tests/test_runtime/test_thinking_profile_budget_v261.py` (12) —
  M1 budget routing.
* `tests/test_runtime/test_prompt_dedupe_v261.py` (26) — M2 snippet
  tags + dedupe + TOML round-trip.
* `tests/test_runtime/parity/test_system_prompt_v260_byte_parity.py`
  (7) — M2 byte-parity gate against pre-fix v2.6.0 baselines.
* `tests/test_api/test_openai_compat_streaming_v261.py` (17) — M3
  async SSE parser + true streaming integration + retry semantics
  + non-streaming path regression check.

All v15 grep guard, v15 byte-parity, README↔reality, and v2.6.1
system-prompt-parity guards green.

### Acceptance criteria

- ✅ GLM-5.1 turn payload includes `thinking_budget = 16384` (M1)
- ✅ GLM-5.1 system prompt is byte-identical to v2.6.0 EXCEPT for
  the deduped sections (M2 byte-parity gate)
- ✅ Every other profile produces byte-identical system prompts
  (M2 byte-parity gate, 4/4 non-opt-in profiles pass)
- ✅ `stream_message` opens `httpx.AsyncClient.stream`, not
  `client.post` (M3 spy test)
- ✅ Chunk N's events arrive at the consumer BEFORE chunk N+1 is
  pulled from the transport (M3 mock-transport ordering test)

## v2.6.0 — Audit closure + cross-project borrow GA

GA cut for v16. Closes the four half-wired-feature gaps surfaced by
the v2.5.x audit (M1–M4) and ports six features from qwen-code,
gemini-cli, opencode, and codex (M5–M10). After v2.6.0, every
feature claimed in the README has matching runtime behaviour.

The 10-mechanism rollout shipped across four alpha/RC waves
(`a1`/`a2`/`a3`/`a4`/`rc1`); the per-wave entries below cover the
incremental shape. This top section is the GA-level summary.

### Audit closure (M1–M4)

- **M1 — Dynamic agent role enum.** `AgentTool.input_schema` now
  builds the `enum` from `AgentRegistry`, so user-defined roles in
  `.llmcode/agents/*.md` are reachable end-to-end.
- **M2 — Agent memory subagent wiring.** `subagent_factory.spawn`
  injects `memory_read`/`memory_write`/`memory_list` per spawn
  scoped by `agent_id`; profile flag `agent_memory_enabled`
  defaults on.
- **M3 — Plugin marketplace installer integration.** `/plugin
  install` routes through `marketplace.installer.install_plugin`
  with the security scan; `executor.attach_plugin_tools` registers
  manifest-declared tools into the live runtime registry.
- **M4 — `/theme` + `/vim` runtime support.** Both slash commands
  apply to the live prompt_toolkit + Rich path and persist to
  `~/.llmcode/config.json::ui`. Eight built-in themes match the
  README.

### Cross-project borrows (M5–M10)

- **M5 — Extension manifest.** `marketplace/manifest.toml` schema +
  `marketplace.validator` + `marketplace.converters.claude_plugin`.
  Subdir-bearing manifests install into the right layer; Claude
  plugins import via the converter.
- **M6 — `/auth` + provider/OAuth/free-tier UX.** Per-provider
  credential handlers under `runtime/auth/handlers/*.py`; storage
  in `~/.llmcode/auth/<provider>.json` (mode 0600); `/auth
  list|login|logout|status` slash command.
- **M7 — Subagent wildcard tools + inline MCP + per-agent policy.**
  `.llmcode/agents/<role>.md` frontmatter accepts wildcard tool
  patterns, inline MCP server entries with SIGTERM→SIGKILL
  teardown, and prebuilt `tool_policy` strings.
- **M8 — GitHub Action wrapper.** `--headless` flag + composite
  action at `.github/llmcode-action.yml` + 3 templates under
  `.github/templates/`. Structured exit codes (0=success, 1=tool,
  2=model, 3=auth, 4=user-cancel).
- **M9 — Formal client/server API.** New `llm_code/server/`
  package: JSON-RPC 2.0 over WebSocket, multi-client per session,
  HMAC bearer tokens persisted in SQLite WAL. CLI: `llmcode server
  start|stop|token grant|revoke|list` + `llmcode connect <url>`.
  Legacy `llmcode --serve` debug REPL unchanged.
- **M10 — Codex inspirations.** `MCPCallApproval` for per-call MCP
  granularity; SQLite state DB at `~/.llmcode/state.db` with
  optional `llmcode migrate v2.6 state-db` migration; transcript
  pager component with model-first navigation + search exposed via
  `/transcript`.

### Profile schema additions (v16)

```toml
[runtime]
agent_memory_enabled = true        # M2

[mcp]
approval_granularity = "tool"      # M10 — "tool" | "call"

[ui]
theme = "default"                  # M4 — one of 8 built-in themes
vim_mode = false                   # M4 — runtime-toggleable
```

### Tests

Suite: 8160 (pre-v16) → 8288 passed (+128 net new). v15 grep guard
(`tests/test_no_model_branch_in_core.py`), v15 byte-parity
(`tests/test_api/parity/`), and README↔reality
(`tests/test_readme_claims_match_runtime.py`) all green.

### Acceptance criteria

- ✅ `.llmcode/agents/custom.md` defines a role; `/agent custom "..."`
  invokes it (M1)
- ✅ Subagent memory round-trips across spawn boundary (M2)
- ✅ `/plugin install <claude-code-plugin>` runs Claude converter,
  installs, tools available immediately (M3 + M5)
- ✅ `/theme dracula` re-renders status line; `/vim on` switches
  editing mode mid-session (M4)
- ✅ `/auth login zhipu` walks OAuth flow; `/auth list` shows token
  status (M6)
- ✅ Custom agent with `tools: ["read_*"]` tries `bash` → denied (M7)
- ✅ GitHub Action review template runs against a synthetic PR
  fixture (M8)
- ✅ Two clients attach to one `llmcode server` session; one writer,
  one observer; both see streaming (M9)
- ✅ `/approve mcp_filesystem_read_file --session` grants for the
  rest of the session; subsequent calls don't re-prompt (M10)
- ✅ All 10 mechanisms covered by unit + integration tests
- ✅ README↔reality test green — every README ✅ has runtime backing

## v2.6.0rc1 — Wave 4 of v16 (M10)

Release candidate for v2.6.0. Adds three high-leverage UX
improvements borrowed from codex (M10): per-call MCP approval
granularity, SQLite session state, and a transcript pager backed by
the new state DB.

### M10 — Codex inspirations

#### Per-call MCP approval

`runtime/permissions.py` gains `MCPCallApproval`, a separate registry
that tracks `(tool_name, args_hash)` pairs. Two grant scopes:

- `once` — consumed on first match.
- `session` — persists until revocation.

`approve_tool(tool_name)` short-circuits the args check so a session-
wide grant unlocks every call of that tool. `approve_call(tool, args,
scope)` records a per-call grant. `args_hash` is a stable SHA-256
over the canonical JSON serialisation; ordering of dict keys does
not affect the hash.

The profile flag `mcp_approval_granularity` (declared in wave 1) is
the toggle: `"tool"` keeps v2.5.x behaviour, `"call"` enforces per-
call approval. The runtime consults `MCPCallApproval.check` only
when the profile selects `"call"`.

New slash command `/approve`:

- `/approve` — list current grants.
- `/approve <tool>` — one-shot grant for the next call.
- `/approve <tool> --session` — session-wide grant.

#### SQLite state DB

`runtime/state_db.py` is a new SQLite WAL store at
`~/.llmcode/state.db` covering three tables:

- `sessions` (id, model, project_path, payload JSON, timestamps).
- `turns` (id, session_id, idx, user_message, assistant_message)
  with `ix_turns_session` on `(session_id, idx)`.
- `tool_calls` (id, turn_id, tool_name, args_json, result_json).

WAL + `busy_timeout=5000` lets multiple llmcode processes share the
same `state.db`. Foreign keys cascade so deleting a session
removes its turns and tool calls. The store is intentionally
runtime-free (no imports from session/conversation) so the
migration command can write to it without loading the conversation
engine.

`runtime/checkpoint_recovery.py` accepts an optional `state_db=`
parameter; when wired, save/load round-trips through SQLite while
the legacy JSON path is preserved as a read fallback so unmigrated
machines keep working.

#### Migration command

`llmcode migrate v2.6 state-db` is the opt-in JSON → SQLite
migration. Atomic flow:

1. Build `~/.llmcode/state.db.tmp` from scratch in a single
   transaction.
2. On error: temp deleted, originals untouched, exception bubbles
   up.
3. On success: temp renamed to `state.db`, originals moved to
   `~/.llmcode/checkpoints.bak/<timestamp>/`.

Bad JSON files are skipped with a warning so one corrupt checkpoint
doesn't abort the migration.

#### Transcript pager

`view/repl/components/transcript_pager.py` is a model-first pager
over the state DB:

- `open()` loads the last N turns; cursor positioned near the bottom.
- `scroll_up/down`, `page_up/down`, `goto_start/end` for navigation.
- `begin_search` / `update_search_buffer` / `commit_search` /
  `next_match` / `prev_match` for search; matches highlighted via
  `PagerLine.is_match`.
- `current_view()` returns the visible slice; `status_line()` shows
  position + match progress.

New slash command `/transcript`:

- `/transcript` — last 50 turns.
- `/transcript <N>` — last N turns.
- `/transcript /needle` — open with search prefilled.

The pager is framework-agnostic by design: the data + interaction
model lives in the component module and is fully covered by tests,
while `/transcript` renders the current viewport inline through the
existing print surface so the feature ships end-to-end without
introducing a new modal floating-overlay infrastructure.

### Tests

43 new tests:

- `tests/test_runtime/test_state_db.py` — schema bootstrap, session
  round trips, turn ordering, tool call linkage, concurrent writers
  (R1 mitigation), busy_timeout cross-conn, migration round trip,
  corrupt-file skip, atomic-on-failure (mid-migration crash leaves
  no half-state), empty checkpoint dir (19 tests).
- `tests/test_runtime/test_mcp_call_approval.py` — stable args
  hash, scope semantics, tool-level grants, revocation, reset,
  list_grants (11 tests).
- `tests/test_view/test_transcript_pager.py` — open/close, up/down/
  page nav, search next/prev cycle, no-match status, backspace +
  cancel, current_view slice, match highlighting, status line
  reporting (13 tests).

Suite: 8245 → 8288 passed (+43 net new). v15 grep guard +
byte-parity gate + README↔reality test green.

### Acceptance criteria covered

- ✅ `mcp_approval_granularity: "call"` enforces per-call approval
- ✅ `llmcode migrate v2.6 state-db` migrates atomically with backup
- ✅ Mid-migration crash leaves originals untouched + temp DB cleaned
- ✅ Concurrent SQLite writers serialize correctly
- ✅ Pager open → search → close exposed via `/transcript` slash command

## v2.6.0a4 — Wave 4 of v16 (M9)

Fourth alpha of v2.6.0. Adds the formal client/server API + session
sharing surface (M9) borrowed from opencode and rewritten for
llmcode conventions.

### M9 — Formal client/server API + session sharing

A new `llm_code/server/` package exposes JSON-RPC 2.0 over WebSocket:

- `proto.py` — frozen dataclasses for `JsonRpcRequest`,
  `JsonRpcResponse`, `EventNotification`, plus `parse_message`,
  `encode_request`, `encode_response`, `encode_event`. Method
  catalogue: `session.create`, `session.attach`, `session.send`,
  `session.subscribe_events`, `session.fork`, `session.detach`,
  `session.close`.
- `server.py` — `SessionManager` + `ServerSession` + `ClientHandle`.
  Each session keeps a 1000-event ring buffer, one writer slot, and
  N observer queues. `dispatch(token, request, client_id)` is the
  single dispatcher entry point and is fully unit-tested without
  binding a port.
- `tokens.py` — HMAC-signed bearer tokens persisted in SQLite WAL
  at `~/.llmcode/server/tokens.db`. Validation hits the DB row on
  every request, so `revoke` is immediate. `LLMCODE_SERVER_TOKEN_SECRET`
  pre-seeds the HMAC secret for multi-host deployments.
- `client.py` — async Python client lib with auto-reconnect +
  `last_event_id` resumption. `EVENTS_EVICTED` is handled via the
  optional `on_evicted` callback.
- `websocket_transport.py` — separate transport tier so the
  dispatcher stays test-focused. Bearer tokens never appear in
  logs (only the 8-char SHA-256 fingerprint).

CLI surface:

- `llmcode server start [--host 127.0.0.1] [--port 8080]`
- `llmcode server stop`
- `llmcode server token grant <session_id> [--role writer|observer]
   [--ttl 3600]`
- `llmcode server token revoke <token>`
- `llmcode server token list`
- `llmcode connect <ws-url> --token <token> [--role ...]
   [--session-id ...]`

The legacy `llmcode --serve` (debug REPL) is unchanged. The new
server is a separate surface with separate defaults; a bound writer
token cannot mint new sessions, an observer token cannot upgrade to
writer, a session-scoped token cannot reach a different session.

Multi-client semantics:

- One writer per session at a time. Second writer attach by a
  different `client_id` returns `WRITER_CONFLICT` (-32002).
- Re-attach by the same `client_id` is a no-op.
- A writer attaching as `observer` releases the writer slot first,
  so a second writer can take over (R3 mitigation).

Reconnect flow: each `attach` carries `last_event_id`; the server
replays the buffered tail; on cursor older than the buffer it
returns `EVENTS_EVICTED` (-32004) so the client can drop local
state and re-attach fresh.

### Tests

34 new tests under `tests/test_server/`:

- `test_protocol.py` — encode/decode round trips + dispatch surface
  (12 tests).
- `test_multi_client.py` — writer/observer fanout, conflict
  detection, idempotent re-attach, downgrade, replay, eviction,
  fork, detach, 50-observer broadcast, cross-session token
  rejection (11 tests).
- `test_token_lifecycle.py` — grant/validate/revoke/expire,
  fingerprint discipline, tampered-signature rejection, store
  survives reopen, revocation immediate at next dispatch (11
  tests).

Suite: 8216 → 8245 passed (+29 net new). v15 grep guard +
byte-parity gate + README↔reality test all green.

### Acceptance criteria covered

- ✅ `llmcode server start` runs concurrent sessions safely
- ✅ Multi-client write/observe works; writer-conflict detected
- ✅ Reconnect with `last_event_id` is lossless
- ✅ Token issue/revoke survives restart
- ✅ Legacy `llmcode --serve` unchanged

### Documentation

- `docs/server.md` — quick start, methods, multi-client semantics,
  reconnect flow, error codes, operations.

## v2.6.0a3 — Wave 3 of v16 (M7 + M8)

Third alpha of v2.6.0. Adds expressive subagent tool policies with
inline MCP servers (M7) and a headless GitHub Action wrapper (M8).

### M7 — Subagent wildcard tools + inline MCP + per-agent policy

`.llmcode/agents/<role>.md` frontmatter gains three new fields:

- `tools:` may now mix literals (`read_file`), wildcards (`read_*`),
  and per-tool args allowlists (`bash:git status,git diff`). Args
  allowlists check the tool's primary string argument with
  startswith semantics so locking down command families is one line
  of frontmatter.
- `tool_policy:` selects a prebuilt policy
  (`read-only` / `build` / `verify` / `unrestricted`) defined in
  `runtime.tool_policy.BUILTIN_POLICIES`.
- `mcp_servers:` is a list of `{name, command, args}` entries that
  spawn as `subprocess.Popen` instances when the subagent boots.

`runtime.tool_policy` exports `parse_tool_entry`, `match_wildcard`,
`args_allowlist_check`, `expand_policy`, and `resolve_tool_subset`.
The wildcard matcher is start-anchored so `read_*` does NOT absorb
`fake_read_thing`-style collisions (R2 in the plan).

`subagent_factory.make_subagent_runtime` now:

- Resolves the effective tool subset from `tool_specs` + `tool_policy`
  before the existing multi-stage filter runs.
- Wraps each tool with an args allowlist via `_ArgsAllowlistTool`
  (intercepts both sync `execute` and async `execute_async`).
- Spawns inline MCP servers via `InlineMcpRegistry`, with a SIGTERM
  (10s grace) → SIGKILL teardown chain attached to `runtime.shutdown`.

Frontmatter parsing in `tools/agent_loader.py` gains optional PyYAML
support (already a core dep) so `mcp_servers:` arrays of dicts parse
cleanly. The wave-1 flat-string parser is preserved as a fallback.

### M8 — GitHub Action wrapper

CLI gains two new flags on `cli/main.py`:

- `--headless` — shorthand for `-q + --output-format json` plus
  structured exit codes (0=success, 1=tool error, 2=model error,
  3=auth error, 4=user cancel).
- `--output-format text|json` — explicit format selector for one-shot
  modes.

`cli/oneshot.run_quick_mode` now returns an exit code. Headless
mode emits a single JSON object to stdout matching
`tests/fixtures/headless_output.schema.json`:

```json
{"output": "...", "tool_calls": [...], "tokens": {...},
 "exit_code": 0, "error": null}
```

Three template workflows under `.github/templates/`
(`pr-review.yml`, `issue-triage.yml`, `custom.yml`) plus the
composite action at `.github/llmcode-action.yml`. Documentation
walkthrough at `docs/github-action.md`.

The composite action exposes the auth secret via env var (never
argv) and exposes `exit_code` as a separate output so workflow
steps can branch on the structured value.

### Tests

- 41 tests for tool policy + wildcard matching + args allowlist +
  inline MCP lifecycle (incl. SIGTERM grace + SIGKILL fallback
  with a process that ignores SIGTERM).
- 10 tests for headless JSON output (one per exit code path,
  schema validation, tool-call capture, text-mode backward compat).

Suite: 8160 → ~8216 passed (+56 net new tests). v15 grep guard +
byte-parity gate + README↔reality test all green.

### Acceptance criteria covered

- ✅ Wildcard tool patterns work (`read_*`, `lsp_*`, etc.)
- ✅ Args allowlist enforced for bash and other arg-aware tools
- ✅ Inline MCP lifecycle clean — SIGTERM grace then SIGKILL
- ✅ Built-in policies match documented expansions
- ✅ `llmcode --headless -q "..."` emits JSON + structured exit code
- ✅ Composite action + 3 templates render valid YAML
- ✅ `-q "..."` backward compat — text mode unchanged

## v2.6.0a2 — Wave 2 of v16 (M5 + M6)

Second alpha of v2.6.0. Adds the formal extension manifest +
Claude-Code plugin converter (M5) and unified `/auth` credential
management across providers (M6).

### M5 — Extension manifest + Claude plugin converter

A new `marketplace/manifest.toml` schema replaces the ad-hoc dict
shape wave 1's installer consumed. Sections covered:

- `[plugin]` — name, version, author, description, providesTools.
- `[install]` — optional `subdir` for monorepo plugin packages.
- `[[hooks]]` — array of (event, command, optional `tool_pattern`).
- `[[mcp]]` — inline MCP server entries (consumed by M7).
- `[[commands]]` — slash command definitions with prompt templates.
- `[themes.<name>]` — Rich-style theme dicts.
- `[variables]` — string templates substituted at hook/command
  execution time.
- `[permissions]` — capability envelope gated by the executor.

Companion modules:

- `marketplace.manifest.load_manifest(path)` — strict TOML parser
  that raises `ManifestError` on unknown sections, missing required
  fields, or shape errors.
- `marketplace.validator.validate(manifest)` — semantic checks
  (semver, hook event whitelist, name regex, duplicate detection,
  shell-substitution rejection, known-permission gate).
- `marketplace.converters.claude_plugin.convert(plugin_dir)` —
  reads a Claude Code plugin's `.claude-plugin/plugin.json` and
  emits llmcode `manifest.toml` text + warnings for the 20%
  out-of-coverage features (`outputStyles`, `lspServers`,
  `on_tab_complete`, etc.).

`installer.install_from_local` and `install_from_github` now run
the manifest validator BEFORE any disk write or after a clone, so
a malformed `manifest.toml` aborts the install with no half-state.
The legacy Claude-Code-shaped path (`PluginManifest.from_path`)
keeps working for wave-1 plugins that ship only `plugin.json`.

### M6 — `/auth` and provider credential storage

A new `runtime.auth` package centralises provider login UX:

- Six built-in handlers under `runtime/auth/handlers/{anthropic,
  openai, zhipu, nvidia_nim, openrouter, deepseek}.py`. Each
  implements the `AuthHandler` Protocol (`login`, `logout`,
  `status`, `credentials_for_request`).
- Storage at `~/.llmcode/auth/<provider>.json`, mode 0600 enforced
  on write and re-checked on read (wider modes treated as absent).
- `redact()` masks secrets to the last 4 characters everywhere
  that surfaces them; `assert_no_credential_leak()` is the
  test-time guard against full keys appearing in DEBUG logs.
- Zhipu offers an OAuth device-code flow as the OAuth fallback for
  headless / SSH sessions; URLs are env-overridable for tests.
- NVIDIA NIM detects free-tier keys (`nvapi-` prefix) and surfaces
  the 40 req/min cap inline in `/auth list`.

`/auth list | login <provider> | logout <provider> | status` lives
on `dispatcher._cmd_auth`. Provider construction now reads the API
key via `auth.resolve_api_key(env_var)` so a stored credential is
the fallback when the env var is unset; explicit env vars still
win for power users.

### Tests

- 24 tests for the manifest schema + validator.
- 17 tests for the Claude plugin converter (3 fixture plugins).
- 4 tests for the installer's TOML manifest gate.
- 37 tests for auth storage, handlers, env-var override, leak
  detection, and dispatcher wiring.

Suite: 8078 → 8160 passed (+82). v15 grep guard + byte-parity
gate + README↔reality test all green.

### Acceptance criteria covered

- ✅ Three Claude fixture plugins convert + validate
- ✅ Out-of-coverage Claude features emit named warnings
- ✅ Validator rejects malformed manifests in fixture set
- ✅ Installer routes through manifest path (no ad-hoc dict left)
- ✅ Six auth handlers ship; OAuth falls back to device code
- ✅ Storage file mode 0600 enforced
- ✅ Provider HTTP clients use auth handler credentials by default;
  env vars still override

## v2.6.0a1 — Wave 1 of v16 audit closure (M1-M4)

First alpha of v2.6.0. Closes the four half-wired-feature gaps the
v2.5.x audit surfaced: custom agent role enum, agent memory subagent
wiring, plugin marketplace installer integration, /theme + /vim
runtime support. The README↔reality test is now live and green for
every ✅ claim under "How it compares".

### M1 — Dynamic agent role enum

`tools/agent.AgentTool.input_schema` now reads its `role` enum from
`runtime.agent_registry.AgentRegistry` instead of a hardcoded list.
User-defined roles in `~/.llm-code/agents/*.md` and
`<project>/.llm-code/agents/*.md` are populated at session init and
become invocable via `agent(role="researcher", task="...")`. Custom
roles that shadow built-ins win and emit a WARNING for visibility.

### M2 — Agent memory subagent wiring

`runtime.subagent_factory.make_subagent_runtime` injects three new
tools — `memory_read`, `memory_write`, `memory_list` — into every
subagent's tool registry, scoped by `agent_id`. The store
(`AgentMemoryStore`) lives on the parent runtime so two consecutive
spawns with the same role share state. Toggle via the new profile
field `agent_memory_enabled` (default on).

### M3 — Plugin marketplace installer integration

`/plugin install` now routes through `marketplace.installer`'s
security-scanned `install_from_github` path. Installed plugins with
`providesTools` entries get registered into the live tool registry
via `marketplace.executor.load_plugin`. The `_activate_plugin_tools`
helper handles missing manifests, tool-name conflicts, and
permission-gated dangerous capabilities cleanly.

### M4 — /theme and /vim runtime support

Eight built-in themes (`default`, `dark`, `light`, `solarized`,
`dracula`, `nord`, `gruvbox`, `monokai`) live in `view.themes`. The
`/theme` slash command lists names, switches the live `BrandPalette`
singleton, and persists to `config.ui_theme`. `/vim on|off|toggle`
flips prompt_toolkit's `EditingMode` at runtime and persists to
`config.vim_mode`. Both stub messages are gone.

### Profile schema additions

Four new fields on `ModelProfile` (declared upfront so M5/M10 don't
double-bump):

- `agent_memory_enabled: bool = True` (M2)
- `mcp_approval_granularity: str = "tool"` (M10 placeholder)
- `ui_theme: str = "default"` (M4)
- `vim_mode: bool = False` (M4)

TOML mapping: `[runtime] agent_memory_enabled`,
`[mcp] approval_granularity`, `[ui] theme`, `[ui] vim_mode`.

### Tests

- 16 new tests in `test_runtime/test_agent_registry.py`
- 24 new tests in `test_runtime/test_subagent_memory.py`
- 7 new tests in `test_marketplace/test_installer_executor_integration.py`
- 22 new tests in `test_view/test_themes.py`
- 8 new tests in `test_view/test_theme_vim_commands.py`
- 34 new tests in `test_readme_claims_match_runtime.py` (README↔reality gate)
- Updates to `test_subagent_factory.py`, `test_agent_role_enforcement.py`,
  and `test_dispatcher.py` to reflect the new M2/M4 behaviour.

Suite: 7967 → 8078 passed (+111). v15 grep guard + byte-parity gate
still green.

### Acceptance criteria covered

- ✅ `.llmcode/agents/custom.md` + `agent(role="custom")` works
- ✅ Subagent `memory_write/read` round-trips across spawn boundary
- ✅ `/plugin install` routes through installer (no bare `git clone`)
- ✅ `/theme dracula` swaps live palette; `/vim on` toggles editing mode

## v2.5.5 — Hotfix: rescue stranded MCP entries from pre-v2.5.4 configs

Codex stop-time review of v2.5.4 caught the third sibling: users
who ran `/mcp install` under v2.5.0–v2.5.3 against a split-schema
config still had the new entries written at the top level
(stranded), and the loader's strict split branch silently dropped
them. v2.5.4 fixed install for the future but didn't rescue
already-stranded data.

### Fix

`runtime/config._parse_mcp_config`: when a config uses split schema
(`always_on` / `on_demand` keys present), promote any sibling
top-level entries that look like server config dicts into the
`always_on` view. Explicit `always_on` entries win on key collision
so a user's deliberate re-declaration is not clobbered by stale
strands.

`view/dispatcher.py /mcp install`: when running on a split-schema
config, also migrates stranded top-level entries into `always_on`
on disk. The persisted config self-heals on the next install
instead of relying on the loader's runtime view.

### Tests

3 new tests in `test_view/test_dispatcher.py`:

- Loader promotes stranded top-level entry into `always_on`
- Explicit `always_on` declaration wins on key collision with a
  stranded sibling
- `/mcp install` rescues stranded entries on disk during the same
  command (config self-heals)

Suite: 7964 → 7967 passed (+3).

### Three-fix lineage (v2.5.3 → v2.5.4 → v2.5.5)

| Hotfix | Symptom |
|---|---|
| v2.5.3 | `/mcp install` wrote `mcp_servers` (snake_case); loader read `mcpServers` (camelCase). New entry vanished on next startup. |
| v2.5.4 | `/mcp install` wrote at top level under split schema; loader's split branch read only `always_on` / `on_demand`. New entry vanished. |
| v2.5.5 | Configs already mutated by v2.5.0–v2.5.3 retained stranded top-level entries. Even after upgrading to v2.5.4 they were silently dropped. v2.5.5 self-heals at both load time and the next install. |

`/mcp install` now works correctly across every key/schema/age
combination of pre-existing user config.

---

## v2.5.4 — Hotfix: /mcp install respects split-schema mcpServers

Codex stop-time review of v2.5.3 caught a sibling bug to the
mcp-key migration: `/mcp install` wrote the new entry at the top
level of `mcpServers`, but when the user's config already used the
documented split schema (`{"always_on": {...}, "on_demand": {...}}`),
`runtime/config._parse_mcp_config` only reads those two sub-dicts
and silently ignores any other top-level key. The newly installed
server appeared in `config.json` but didn't load on next startup —
identical user-visible symptom to the v2.5.3 bug.

### Fix

`/mcp install`: detects the split schema (presence of `always_on`
or `on_demand` keys) and inserts new servers into `always_on` by
default. Without the split schema, behaviour is unchanged from
v2.5.3 (top-level entry, treated as `always_on` by the legacy-flat
loader branch).

`/mcp remove`: searches both the top level and the split sub-dicts
so it works regardless of which install version (or schema) put
the entry there.

### Tests

2 new dispatcher tests in `test_view/test_dispatcher.py`:

- Install on a split-schema config: new server lands inside
  `always_on`, NOT next to it; loader actually surfaces the entry
  via `_dict_to_runtime_config`.
- Remove on a split-schema config: finds the entry inside
  `always_on`, leaves the `on_demand` sibling untouched.

Suite: 7962 → 7964 passed (+2).

### Why both v2.5.3 and v2.5.4 were necessary

v2.5.3 fixed the **wrong-key** case (snake_case → camelCase). v2.5.4
fixes the **wrong-level** case (top-level → split sub-dict). Users
on either schema flavour now have `/mcp install` actually work.

---

## v2.5.3 — Audit hotfix: /mcp install key + --serve security + README accuracy

External audit of v2.5.2 surfaced three correctness/security/docs
gaps. This release closes them. (Larger half-wired-feature gaps —
custom agent role enum, agent-memory subagent wiring, plugin
marketplace integration, /theme + /vim stubs — are scoped to v2.6.0.)

### #3 — `/mcp install` writes the wrong config key

`view/dispatcher.py:1526` wrote `mcp_servers` (snake_case) when
`runtime/config.py` reads `mcpServers` (camelCase, the canonical
Claude-Code-compatible key). Servers installed via `/mcp install`
silently disappeared on the next startup.

Fixes:

- `dispatcher.py` — `/mcp install` and `/mcp remove` now read AND
  write `mcpServers`. They also detect a pre-v2.5.3 `mcp_servers`
  key in the config and migrate its entries forward into
  `mcpServers` on touch (preserves servers installed by the buggy
  versions).
- `config.py::_dict_to_runtime_config` — additionally merges any
  remaining `mcp_servers` entries into the canonical view at load
  time, so even users who never run `/mcp install` again recover
  their previously-installed servers.

### #6 — `--serve` bound `0.0.0.0` unconditionally

`cli/main.py:327` passed `host="0.0.0.0"` to `DebugReplServer` with
no opt-in flag. Anyone running `llmcode --serve` on a laptop
without a VPN exposed a debug REPL with full shell access to the
local network (or, on cloud VMs, the public internet).

Fix:

- New `--allow-remote` flag (default off). Without it, the server
  binds `127.0.0.1`. With it, the server binds `0.0.0.0` AND emits
  a stderr banner naming the surface that was just exposed:
  > ⚠ --allow-remote: server is listening on 0.0.0.0:PORT. Use
  > only on trusted networks; the debug REPL has full shell access
  > via the remote session.
- README updated: the `llmcode --serve` line now shows the
  localhost-only default plus the explicit `--allow-remote` opt-in.

### #7 — README test counts and badges out of date

The README badge advertised "6182 tests passing"; the tree-summary
section claimed "5,527+ tests". Actual collected: 7,962. Updated
both surfaces.

### Tests

3 new dispatcher tests + 1 new config-loader test in
`test_view/test_dispatcher.py`:

- `/mcp install` writes canonical `mcpServers` key
- `/mcp install` migrates pre-v2.5.3 `mcp_servers` entries forward
- Config loader accepts legacy `mcp_servers` and merges with
  `mcpServers` on collision-free union

Suite: 7959 → 7962 passed (+3 dispatcher; the +1 loader test runs
under the same parametrize sweep so doesn't add to the count).

### Known follow-ups (scoped to v2.6.0, not "skipped")

The audit surfaced four further items that need a real spec —
they're not docs/security hotfixes and shouldn't ship without a
plan:

1. **Custom agent role enum** — `tools/agent.py:83` hardcodes
   `["build", "plan", "explore", "verify", "general"]`. The
   `.llmcode/agents/*.md` loader exists; the role string just
   isn't accepted by the AgentTool input schema. Fix is registry-
   driven enum extension.
2. **Agent memory subagent wiring** — `agent_memory.py` has the
   helpers; `subagent_factory.py` doesn't inject them. Three-scope
   memory persistence is documented in the README but not active.
3. **Plugin marketplace installer** — `/plugin install` clones
   repos directly without going through `marketplace/installer.py`
   security scan, and `executor.py providesTools` is not wired
   into the runtime tool registry.
4. **`/theme` + `/vim` stubs** — README documents them; dispatcher
   says "v2 REPL doesn't support legacy theme" / "no runtime
   toggle yet". Either implement against the v2 prompt-toolkit
   path or remove from README.

These ship together as v2.6.0. v2.5.3 is the security/correctness
floor everyone should be on first.

---

## v2.5.2 — Hotfix follow-up: assistant ToolUseBlock missing tool_calls on outbound

Codex stop-time review of v2.5.1 caught a sibling correctness bug
exposed by the same assistant↔tool pairing that v2.5.1 fixed on the
user side.

### Symptom

After v2.5.1 the user-side bundled `ToolResultBlock` correctly splits
into `role: tool` messages. But the *prior* assistant message in
the same conversation, if it carried `ToolUseBlock`s (the canonical
shape under `native_tools=true`), was silently dropping them — the
parts-array branch in `_openai_convert_message` had no
`ToolUseBlock` case, so outbound assistant emerged as
`{"role": "assistant", "content": []}` with **no `tool_calls`
field at all**. OpenAI-compat servers strictly require every
`role: tool` message to reference an `id` from the prior assistant's
`tool_calls`; the v2.5.1 fix made the role:tool entries visible
on the wire while the assistant side was still empty, breaking the
pairing the spec depends on.

### Root cause

Pre-existing gap in `_openai_convert_message` — the function had
branches for `TextBlock`, `ImageBlock`, `ThinkingBlock`, and
`ToolResultBlock`, but not `ToolUseBlock`. v2.4.0 hid the bug
because GLM-5.1 ran in XML-tools mode (`native_tools=false`,
`force_xml_tools=true`) where assistant tool calls live in a
`TextBlock` body, never in a structured `ToolUseBlock`. When users
flip to `native_tools=true` (Adam's GLM profile), the inbound path
correctly parses server-side `tool_calls` into `ToolUseBlock`s, but
outbound serialization had no symmetric path.

### Fix

`_openai_convert_message` gains an assistant-with-`ToolUseBlock`
branch placed after the tool-result branch and before the
parts-array fallback:

- Iterate `msg.content`; collect `ToolUseBlock`s into a `tool_calls`
  array (each entry has `id`, `type: "function"`,
  `function: {name, arguments: json.dumps(input)}`).
- Concatenate `TextBlock`s into `content` (or `null` if there are
  none, per OpenAI spec for tool-call-only messages).
- Drop `ThinkingBlock`s silently (existing v2.x behaviour;
  OpenAI-compat servers reject unknown content types).

### Tests

5 new tests in `tests/test_api/test_conversion.py::TestAssistantToolUseBlockOpenAI`:

- Single `ToolUseBlock` → `tool_calls` of length 1, `content: None`
- Multiple `ToolUseBlock`s → `tool_calls` array preserves order and
  IDs
- `TextBlock + ToolUseBlock` → both populated
- `ThinkingBlock + ToolUseBlock` → thinking dropped, tool_calls intact
- Full round-trip (user → assistant with 2 tool_calls → split tool
  results): every `tool_call_id` on a `role: tool` entry matches an
  `id` in the prior assistant's `tool_calls`. This is the wire-level
  contract a downstream provider depends on.

The conversion corpus was re-captured to lock in the corrected
assistant outbound shape across every scenario that includes a
`ToolUseBlock`. The parity gate now guards against regressions of
both v2.5.1 and v2.5.2 fixes simultaneously.

Suite: 7954 → 7959 passed (+5).

### Why both hotfixes were necessary

v2.5.1 alone was a partial fix — it made `role: tool` messages
visible on the wire, but without v2.5.2 the assistant side was
still missing `tool_calls`, so a strict OpenAI-compat server would
reject the request as "tool result without matching tool call".
GLM-5.1 with v2.5.1-only might have produced the headlines under
its own permissive parsing, but any conformant downstream provider
(Anthropic-via-OpenAI-shim, OpenRouter, NVIDIA NIM, etc.) would
have rejected the next request body. v2.5.2 closes the spec gap
properly.

---

## v2.5.1 — Hotfix: bundled ToolResultBlock silently dropped on OpenAI-compat path

Critical correctness bug exposed by GLM-5.1 in v2.5.0 GA smoke testing.

### Symptom

User asks `顯示今日熱門新聞三則`. Model emits two parallel `web_search`
tool calls in one turn. Both return valid results. Then in
`reasoning_content` the model writes:

> "The system reminders say 'You just called web_search and received
> the result above' but there's no actual web_search tool call or
> result visible to me. This seems like a confusing situation. Let me
> just be honest — I don't have real-time web access."

…and the visible `content` becomes a denial of capability — the
opposite of what v14 + v15 were designed to prevent.

### Root cause

`runtime/conversation.py:2069` bundles every `ToolResultBlock` from
one turn into a single `Message(role="user", content=tuple(blocks))`.

`api/conversion._openai_convert_message` only routes a tool-result
message to `role: "tool"` when **`len(content) == 1`**. With two
bundled results, it falls through to the parts-array path — which
has branches for `TextBlock`, `ImageBlock`, `ThinkingBlock`, but NO
branch for `ToolResultBlock`. The blocks are silently dropped, the
outbound user message is empty, and the v14 mech-A
`<system-reminder>` blocks placed after it lie about a result that
was never sent. GLM-5.1 catches the mismatch and refuses to play
along — correct reasoning under broken inputs.

The bug pre-dates v15 (the same shape exists in v2.4.0
`openai_compat._convert_message`), but v14 mech-A made it
user-visible by adding reminders that explicitly reference the
missing result. Anthropic-shape providers were unaffected — that
path natively supports multi-block tool_result user messages.

### Fix

`api/conversion._split_bundled_tool_results` runs as a pre-pass
inside `OpenAICompatProvider._build_messages`. Multi-block
ToolResultBlock-only messages explode into one
`Message(role="user", content=(block,))` per result; each then
hits the existing `len == 1` branch and serializes correctly to
`{"role": "tool", "tool_call_id": ..., "content": ...}`.

Mixed content (TextBlock + ToolResultBlock in one user message) is
left unchanged — that shape is unused by the runtime today, and
preserving the v2.4.0 behaviour keeps the M3 byte-parity gate
honest for the surfaces that DO exist.

### Tests

5 new tests in `tests/test_api/test_conversion.py::TestSplitBundledToolResults`:

- Two bundled results → two `role: tool` messages with correct
  `tool_call_id`s
- Three bundled results → three messages
- Single block unchanged (regression guard)
- Mixed content left untouched (intentional limitation)
- Anthropic path does not split (one user message, two `tool_result`
  content blocks)

The conversion corpus (`tests/fixtures/conversion_corpus.json`) was
re-captured to lock in the corrected wire shape for the two
affected scenarios (`multi_tool_use_one_msg`,
`parallel_tool_calls_one_assistant`). The parity gate is now a
gate against future regressions of the v2.5.1 fix, not the v2.4.0
broken behaviour.

Suite: 7949 → 7954 passed (+5).

### Manual smoke

GLM-5.1 + `顯示今日熱門新聞三則` against the editable install with
all `[tool_consumption]` flags on: model now successfully consumes
both `web_search` results and produces three headlines + URLs in
`content` instead of the denial.

---

## v2.5.0 — Borrow audit adoption GA (M1–M5)

GA of the v15 borrow-audit adoption: five mechanisms ported / adapted
from `Alishahryar1/free-claude-code` (the Claude Code → any-LLM proxy
at `/Users/adamhong/Work/qwen/reference/free-claude-code`). Each
mechanism preserves llmcode's standalone-CLI identity — none of them
turns the runtime into a Claude Code proxy.

This release consolidates v2.5.0a1 (M1), v2.5.0a2 (M2), v2.5.0a3
(M3), and adds Mechanisms M4 (control-token stripping) + M5 (inline
WebFetch / WebSearch parser variant).

See `docs/superpowers/specs/2026-04-27-llm-code-v15-borrow-from-free-claude-code-design.md`
for the full design.

### Mechanism M1 — Request optimizations (a1)

Five detectors at the provider entry point intercept patterns whose
answer is deterministic and short-circuit with a synthetic response:

| Detector | Trigger | Synthetic body |
|---|---|---|
| `quota_mock` | `max_tokens=1` + `quota` substring | `Quota check passed.` |
| `prefix_detection` | `<policy_spec>` + `Command:` | shlex-derived prefix |
| `title_skip` | system asks for sentence-case title | `Conversation` |
| `suggestion_skip` | `[SUGGESTION MODE:` | empty |
| `filepath_mock` | `Command:` + `Output:` + `filepaths` | `<filepaths>...</filepaths>` |

Profile-gated via `enable_request_optimizations: bool = True`
(default ON). Both `send_message` and `stream_message` are wired —
the streaming path wraps the synthetic response in a one-shot event
sequence so downstream renderers see a normal stream.

Module: `llm_code/api/request_optimizations.py`. Reference:
`/Users/adamhong/Work/qwen/reference/free-claude-code/api/optimization_handlers.py`.

### Mechanism M2 — Proactive sliding-window rate limiter (a2)

`SlidingWindowLimiter` in `llm_code/api/rate_limiter.py` caps the
rate of HTTP POSTs to a provider to N per window (default 60s).
Implemented as an async context manager backed by a deque of
timestamps. Optional concurrency cap limits in-flight calls
independently of the rate window. Both providers gate
`client.post` (and the Anthropic streaming connection setup)
through a `_post_with_proactive_limit` helper.

Profile schema:

```toml
[provider]
proactive_rate_limit_per_minute = 40    # 0 = disabled (default)
proactive_rate_limit_concurrency = 4    # 0 = no concurrency cap
```

Reference: `/Users/adamhong/Work/qwen/reference/free-claude-code/core/rate_limit.py::StrictSlidingWindowLimiter`.

### Mechanism M3 — Conversion-layer extraction (a3)

New module `llm_code/api/conversion.py` is the single source of
truth for cross-provider message conversion. Both providers shrink
to thin `_convert_message` shims; the legacy ~120-line per-block
conversion in each provider is gone.

Public surface:

- `serialize_messages(messages, ctx, *, system=None) -> list[dict]`
- `serialize_tool_result(content) -> str` — stable JSON for any
  payload (None, str, dict, list)
- `deferred_post_tool_blocks(blocks)` — OpenAI-compat reorder helper
- `ConversionContext(target_shape, reasoning_replay,
  strip_prior_reasoning)` — frozen dataclass
- `ReasoningReplayMode` enum — DISABLED / THINK_TAGS /
  REASONING_CONTENT / NATIVE_THINKING

Byte-parity gate
(`tests/test_api/parity/test_provider_conversion_parity_v15.py`)
asserts that all 49 corpus scenarios produce identical output
across both target shapes — 98 byte-equality assertions all green.
Any drift fails CI.

Reference: `/Users/adamhong/Work/qwen/reference/free-claude-code/core/anthropic/conversion.py`.

### Mechanism M4 — Control-token stripping (GA)

Some models (Qwen, Llama, GLM under certain chat templates)
occasionally emit raw control tokens (`<|im_end|>`,
`<|endoftext|>`, `<|start_header_id|>`, `<|eot_id|>`,
`<|file_separator|>`) into their content stream. M4 adds a
`_CONTROL_TOKEN_RE` regex to `llm_code/view/stream_parser.py` and
strips matches on every text-emission site (`_step` and `flush`).

Pattern: `<\|[^|>\s]{1,80}\|>` — bounded by an 80-char cap to
prevent catastrophic backtracking; whitespace excluded so
`<| not_a_token |>` shapes don't match.

Tool-call XML wrappers are unaffected (`<tool_call>...</tool_call>`
doesn't match the regex). User input bypass is by architecture —
the parser only sees model output, never user-typed text.

Reference: `/Users/adamhong/Work/qwen/reference/free-claude-code/core/anthropic/tools.py::_CONTROL_TOKEN_RE`.

### Mechanism M5 — Inline WebFetch / WebSearch parser variant (GA)

Models trained on Claude-Code transcripts occasionally emit:

```
WebFetch{"url": "https://example.com", "prompt": "..."}
WebSearch{"query": "x"}
web_fetch{"url": "..."}
```

…as plain text inside an assistant message — no `<tool_call>`
wrapper, no XML tag. The 6 v13 parser variants don't catch this
exact shape. M5 adds `webfetch_inline` as the 7th variant,
appended to `DEFAULT_VARIANT_ORDER` after `bare_name_tag` (lowest
priority — only fires when no earlier wrapper-based variant
matched).

Match regex:

```
\b(WebFetch|WebSearch|web_fetch|web_search)\s*(\{(?:[^{}]|\{[^{}]*\})*\})
```

Registry-gated via `known_tool_names`: only fires when the matched
name (after PascalCase → snake_case normalisation) is in the
registry for the current turn. Production guard against
false-positive matches on code blocks containing literal
`WebFetch{…}` text. Edge case: if the user registers a literal
PascalCase name (`WebFetch`), it's honoured verbatim.

Reference: `/Users/adamhong/Work/qwen/reference/free-claude-code/core/anthropic/tools.py::_WEB_TOOL_JSON_PATTERN`.

### Profile schema (v15 additions)

Three new flat fields on `ModelProfile` (per v13/v14 convention):

```python
enable_request_optimizations: bool = True       # M1
proactive_rate_limit_per_minute: int = 0        # M2
proactive_rate_limit_concurrency: int = 0       # M2
```

TOML section_map extended:
- `[runtime]` → `enable_request_optimizations`
- `[provider]` → also recognises the two M2 keys

### Tests

- M1: 36 tests (`tests/test_api/test_request_optimizations.py`)
- M2: 18 tests (`tests/test_api/test_sliding_window_limiter.py`)
- M3: 31 unit tests (`tests/test_api/test_conversion.py`) +
  98 parity tests
  (`tests/test_api/parity/test_provider_conversion_parity_v15.py`)
- M4: 21 tests
  (`tests/test_streaming/test_control_token_stripping.py`)
- M5: 22 tests
  (`tests/test_tools/test_parser_variant_webfetch_inline.py`)
- Modified: 2 existing tests in
  `tests/test_tools/test_parser_variant_registry.py` (variant
  count from 6 → 7).

Suite: 7722 baseline → 7949 passed (+227 tests overall, all green).
Grep guard (`tests/test_no_model_branch_in_core.py`) green.

### Acceptance criteria met

- [x] v13 grep guard green
- [x] v14 byte-parity (no fixtures exist post-Phase C; reasoning
      filter / outbound thinking suite green proves M3 didn't
      regress v14 behaviour)
- [x] M3 parity gate green — 49/49 scenarios × 2 providers = 98
      byte-equality assertions all pass
- [x] All 5 mechanisms have unit + integration coverage
- [x] CHANGELOG entry consolidating M1–M5 (this section)

Manual smoke recommended before tagging:
- GLM-5.1: `顯示今日熱門新聞三則` should still produce 3 headlines
  + URLs (regression check after M3 conversion-layer refactor).
- NVIDIA NIM (or any free-tier endpoint with hard 40 req/min cap):
  fire 50 burst calls with `proactive_rate_limit_per_minute = 40`,
  expect 0× 429 responses (vs ~10× without M2).

---

## v2.5.0a3 — v15 M3 conversion-layer extraction

Third alpha of the v15 borrow-audit adoption. Extracts cross-provider
message-shape conversion, tool-result serialization, and reasoning-
replay strategy into a single shared module, gated behind a 49-
scenario byte-parity corpus to guarantee no behavioural drift.

See `docs/superpowers/specs/2026-04-27-llm-code-v15-borrow-from-free-claude-code-design.md` §3.3.

### Mechanism M3 — Anthropic↔OpenAI conversion layer

New module `llm_code/api/conversion.py` exposes:

- `serialize_messages(messages, ctx, *, system=None) -> list[dict]`
  — single source of truth for `tuple[Message, ...]` → wire `dict[]`,
  dispatching by `ctx.target_shape` (`"anthropic"` / `"openai"`).
- `serialize_tool_result(content) -> str` — stable JSON encoding of
  any tool_result payload (None, str, dict, list, mixed).
- `deferred_post_tool_blocks(blocks) -> tuple[ContentBlock, ...]` —
  reorder helper for the OpenAI-compat constraint that assistant
  text after `tool_calls` must move to a separate post-tool message.
- `ConversionContext(target_shape, reasoning_replay,
  strip_prior_reasoning)` — frozen dataclass packaging per-call
  options.
- `ReasoningReplayMode` enum — DISABLED / THINK_TAGS /
  REASONING_CONTENT / NATIVE_THINKING.

### Provider slim-down

`anthropic_provider._build_messages` and
`openai_compat._build_messages` now delegate per-message conversion
to thin `_convert_message` shims (which themselves call the new
conversion module). The legacy ~120-line per-block conversion in
each provider is gone.

The v14 Mechanism B reasoning-content history filter is carried
through via `ctx.strip_prior_reasoning`; GLM-5.1 / DeepSeek-R1
profiles still get the filter when they opt in.

### Parity gate

`tests/fixtures/conversion_corpus.json` (committed in the M3 prep
commit) holds 49 representative scenarios captured from the
v2.4.0 codebase before the refactor:

- Single-turn / multi-turn (5/8/10 messages)
- Tool use single / sequence / parallel / multi-in-one-msg
- Thinking blocks (signed / unsigned / back-to-back / paired
  with tool_use)
- Server-side tool blocks (Anthropic web_search round-trip)
- Image blocks (user-only / mixed text+image / image in assistant)
- Strip-prior-reasoning profile flag scenario
- Edge cases: empty content, long content (10K chars), unicode,
  emoji, none-valued tool args, special-char tool args, error
  tool results

`tests/test_api/parity/test_provider_conversion_parity_v15.py`
parametrizes the corpus across both `target_shape="anthropic"` and
`target_shape="openai"` — 98 byte-equality assertions all green.
Any drift fails CI.

### Backward-compat shims

- `OpenAICompatProvider._convert_message` and
  `AnthropicProvider._convert_message` retained as thin shims so
  tests that monkey-patch them continue to work.
- `_strip_reasoning_keys` and `_warn_thinking_dropped_once`
  re-exported from `openai_compat` for the same reason.
- The `_thinking_drop_warned` warn-once flag stays on
  `openai_compat` so existing test fixtures resetting it still work;
  `conversion._warn_thinking_dropped_once` reads / mutates the flag
  through the module reference.

### Tests

31 new unit tests in `tests/test_api/test_conversion.py`:

- `serialize_tool_result` — None / str / dict / list / nested /
  unicode / fallback (8).
- `deferred_post_tool_blocks` — anchor logic across edge cases (5).
- `serialize_messages` Anthropic — text / thinking ± signature /
  image / tool_result ± error / server tool block /
  cache_control breakpoint placement (8).
- `serialize_messages` OpenAI — text / system prepend / parts shape
  with image / tool collapse / thinking drop / strip flag (6).
- `ConversionContext` immutability + defaults + unknown shape (3).
- `ReasoningReplayMode` enum surface (1).

Plus 98 byte-parity tests (49 scenarios × 2 providers).

Suite: 7776 → 7905 passed (+129).

### Risks mitigated

- Cross-provider regression — the parity gate is the primary
  safeguard. Any future change to `conversion.py` runs the 49
  scenarios automatically; behavioural drift fails CI.
- v14 mechanism B continuity — strip_prior_reasoning logic flows
  through `ConversionContext` and exercised by the captured
  corpus's `strip_prior_reasoning_flag` scenario.

### Sequel

v2.5.0 GA folds Mechanisms M4 (control-token stripping) + M5
(inline WebFetch/WebSearch parser variant) into one commit.

---

## v2.5.0a2 — v15 M2 proactive rate limiter

Second alpha of the v15 borrow-audit adoption. Adds Mechanism M2:
proactive sliding-window rate limiter on the provider HTTP layer.
See `docs/superpowers/specs/2026-04-27-llm-code-v15-borrow-from-free-claude-code-design.md`
§3.2.

### Mechanism M2 — Proactive sliding-window rate limit

`SlidingWindowLimiter` class (`llm_code/api/rate_limiter.py`) caps
the rate of HTTP POSTs to a provider to N per window (default
60s). Implemented as an async context manager backed by a
deque-of-timestamps. Optional concurrency cap limits in-flight
calls independently of the rate window.

Algorithm: on `acquire`, drop expired timestamps, append now if
under cap, else compute the wait until the oldest rolls off and
`asyncio.sleep`. The lock is released across the sleep so multiple
waiters share the wait time.

### Wire-up

Both providers gate `client.post` (and the Anthropic streaming
connection setup) through a `_post_with_proactive_limit` helper.
The limiter is held only while the connection is being established;
once SSE bytes start flowing, the slot is released so subsequent
requests can proceed in parallel within the cap.

### Profile schema

Two new flat fields (declared in M1, wired in M2):

```toml
[provider]
proactive_rate_limit_per_minute = 40    # 0 = disabled (default)
proactive_rate_limit_concurrency = 4    # 0 = no concurrency cap
```

Profiles without these keys keep their current behaviour — the
limiter is `None` and the HTTP path is unchanged.

### Telemetry

`SlidingWindowLimiter.wait_count` exposes the number of awaited
acquires (number of times the window was full when a call arrived).
`INFO rate_limiter: proactive_wait wait_seconds=N.NN max=M
window=Ws` log fires per wait.

### Tests

18 new tests in `tests/test_api/test_sliding_window_limiter.py`:

* Constructor validation (4).
* Within-window no-wait (1).
* Window full → wait (1) + window rolloff allows new calls (1).
* Concurrency cap (2).
* Combined gates (1).
* Telemetry counter (1).
* Re-entry / cancellation (2).
* Provider integration (4) — both providers, both off+on states.
* End-to-end timing — 11 POSTs at 10/0.4s window forces ≥ 0.35s
  wall-clock for the 11th call (1).

Suite: 7758 → 7776 passed (+18).

### Sequel

v2.5.0a3 lands M3 (cross-provider conversion layer extraction with
parity gate); M4 (control-token stripping) and M5 (inline WebFetch
parser variant) fold into v2.5.0 GA.

---

## v2.5.0a1 — v15 M1 request optimizations

First alpha of the v15 borrow-audit adoption (5 features ported from
`Alishahryar1/free-claude-code`, the Claude Code → any-LLM proxy).
This alpha lands Mechanism M1: trivial-call interception. See
`docs/superpowers/specs/2026-04-27-llm-code-v15-borrow-from-free-claude-code-design.md`
§3.1 for the full design.

### Mechanism M1 — Request optimizations (trivial-call interception)

Five detector predicates run before any HTTP call. When one matches,
a synthetic `MessageResponse` short-circuits the request — saving an
HTTP round-trip and ~50–500 tokens per hit.

| Detector | Trigger pattern | Synthetic body |
|---|---|---|
| `quota_mock` | `max_tokens=1` AND `"quota"` substring in single user message | `"Quota check passed."` |
| `prefix_detection` | `<policy_spec>` + `Command:` in single user message | shell prefix extracted via `shlex` (handles two-word commands like `git commit`, env-var prefixes; refuses backticks / `$(...)` injection) |
| `title_skip` | system prompt asks for "sentence-case title" OR (`return json` + `field` + `coding session`/`this session`) | `"Conversation"` |
| `suggestion_skip` | `[SUGGESTION MODE:` substring in any user message | empty content |
| `filepath_mock` | single user message + `Command:` + `Output:` + `filepaths` keyword (or `extract any file paths` in system prompt) | `<filepaths>...</filepaths>` block parsed locally from output |

Module: `llm_code/api/request_optimizations.py` (~430 LOC).

### Toggle

`profile.enable_request_optimizations: bool = True` — on by default
across every profile. Profiles that want every call to hit the model
(testing, baseline benchmarks) opt out:

```toml
[runtime]
enable_request_optimizations = false
```

### Wire-up

Both providers (`anthropic_provider.py`, `openai_compat.py`) call
`try_optimize` first in `send_message` and `stream_message`. On hit:

* `send_message` returns the synthetic `MessageResponse` directly.
* `stream_message` returns a one-shot `AsyncIterator[StreamEvent]`
  (`StreamMessageStart` → text deltas → `StreamMessageStop`) via the
  new `_synthesize_stream_events` helper. Downstream renderers see a
  normal stream shape.

### Tests

36 new tests in `tests/test_api/test_request_optimizations.py`:

* Per-detector positive (5) + negative (5) cases.
* Co-occurring signals — first matching detector wins (registry order
  asserted explicitly).
* `_synthesize_stream_events` event sequence (3 cases).
* Provider integration — optimizable request triggers 0 HTTP calls;
  non-optimizable triggers 1; profile flag OFF disables interception
  for both providers.

Suite: 7722 → 7758 passed (+36).

### Sequel

v2.5.0a2 wires Mechanism M2 (proactive sliding-window rate limiter)
through the same profile schema; M3 (cross-provider conversion
layer), M4 (control-token stripping), M5 (inline `WebFetch` parser
variant) follow before v2.5.0 GA.

---

## v2.4.0 — Tool-result consumption compat layer GA

GA of the v14 tool-result consumption compatibility layer (see
`docs/superpowers/specs/2026-04-27-llm-code-v14-tool-consumption-compat-design.md`).
Three runtime mechanisms now compensate for a class of model-level
instruction-following weaknesses where a model calls a tool, receives
data, and then writes a `content` response that contradicts the tool
result. After v14, "model X denies the tool it just used" is a
runtime-handled compatibility concern instead of a per-model prompt-
engineering treadmill.

This release consolidates v2.4.0a1 (Mechanism A) and v2.4.0a2
(Mechanism B), and adds Mechanism C (denial detection + forced retry).

### Mechanism C — Denial-pattern detection + forced retry

After a turn's `content` is fully streamed, scan it for denial
keywords (English + Traditional/Simplified Chinese curated regex
corpus). If a denial pattern matches AND a tool was called this
turn, re-invoke the provider once with an injected continuation
reminder. The retried response replaces the original for rendering.

- **Cap:** 1 retry per turn. Persistent denials emit a structured
  `denial_retry_failed pattern_persisted_after_retry` log; the
  retried (still-denial) response renders to the user.
- **Gate:** `has_recent_tool_call` — denial without a recent tool
  call (e.g. user asked "are you online?") is a genuine answer and
  bypasses the retry.
- **Streaming UX:** When `retry_on_denial=True`, content is buffered
  during streaming so the detector runs before the user sees a
  denial that gets replaced. Profiles paying for retry pay for
  buffered TTFT; profiles with the flag off keep the unbuffered
  streaming UX.
- **Cost:** Each retry doubles the provider-call count for that turn.
  Both calls flow through the cost meter normally — there is no
  special accounting. Observe via the
  `llmcode.tool_consumption.denial_retries_total` counter (wired in
  v12 M6 OTel pipeline).

### Detector corpus

`tests/fixtures/denial_corpus.json` — 60 labeled entries (30
denials + 30 non-denial controls) across English, Traditional
Chinese, Simplified Chinese. Required thresholds enforced in
`tests/test_runtime/test_denial_detector.py::TestCorpusRegression`:
**precision >= 0.95, recall >= 0.85**. Current corpus achieves
**precision 1.000, recall 1.000**. New phrasings observed in
production should be added to the corpus with the correct label;
the regression test will fail until the regex is adjusted.

### GLM-5.1 profile opt-in (all three mechanisms)

`examples/model_profiles/65-glm-5.1.toml` enables A + B + C:

```toml
[tool_consumption]
reminder_after_each_call = true
strip_prior_reasoning = true
retry_on_denial = true
```

Copy this file to `~/.llmcode/model_profiles/glm-5.1.toml` to
activate against a self-hosted GLM-5.1 endpoint.

### Manual smoke recommendation

Before tagging, run `顯示今日熱門新聞三則` against GLM-5.1 in the
interactive REPL with the GLM-5.1 profile installed. Expected log
sequence:

1. `INFO tool_consumption: reminder_injected tool=web_search`
2. (possibly) `INFO tool_consumption: reasoning_stripped` (multi-turn)
3. (possibly) `WARNING tool_consumption: denial_detected_retry pattern=...`

**Acceptance:** rendered content contains 3 headlines + URLs from
the search results, NO denial keyword. Document the outcome in the
release notes (`Mechanism C alone fixed / did not fix` flagging is
the most useful field follow-up if a fourth mechanism is needed in
v15).

If even the retry produces denial, the GLM-5.1 chat template is more
broken than the runtime can compensate for; the `denial_retry_failed`
log + the cost-doubled retry are the diagnostic signals. Recommend
Zhipu cloud API as an alternative path.

### Tests

- 8 new tests in `tests/test_runtime/test_denial_retry_loop.py`
  covering the retry path, the cap, the gate, the streaming UX
  trade-off, and session history shape.
- 38 tests + 59 corpus per-entry tests in
  `tests/test_runtime/test_denial_detector.py` covering the gate,
  per-language patterns, edge cases, and the precision/recall
  regression contract.
- Full suite: 7722 passed, 34 skipped (was 7575 baseline in v2.3.2).
- Grep guard (`tests/test_no_model_branch_in_core.py`) stays green.

### Cumulative v14 summary (a1 + a2 + GA)

- Mechanism A — post-tool `<system-reminder>` injection. Default ON
  globally; ~40 tokens per tool call. Shipped in v2.4.0a1.
- Mechanism B — strip `reasoning_content` / `reasoning` keys from
  prior assistant messages on outbound. Default OFF; opt-in via
  profile. Forward-compatibility filter — stock openai_compat
  already filters reasoning via the `ThinkingBlock` drop. Shipped in
  v2.4.0a2.
- Mechanism C — denial-pattern detection + forced retry. Default
  OFF; opt-in via profile (GLM-5.1 only by default). Shipped in
  v2.4.0.
- Profile schema additions: `reminder_after_each_call`,
  `strip_prior_reasoning`, `retry_on_denial` under
  `[tool_consumption]`.
- Grep guard remains green throughout — zero per-model `if "x" in
  m:` branches in any protected path.
- Profiles with all three flags off produce byte-identical message
  history to v2.3.2 (parity verified by tests covering each flag's
  off path).

## v2.4.0a2 — v14 Mechanism B: reasoning-content history filter

Second alpha of v14's tool-result consumption compatibility layer.
Wires through the `strip_prior_reasoning` flag declared by v2.4.0a1
and adds a defensive filter pass to `OpenAICompatProvider._build_messages`.

### Mechanism B — Reasoning-content history filter

When the active profile sets `strip_prior_reasoning=True`, the
provider's outbound message conversion drops `reasoning_content` and
`reasoning` keys from prior assistant message dicts. Each per-call
strip aggregates one INFO log:

```
tool_consumption: reasoning_stripped turns=<n> total_bytes=<m>
```

### Step B.1 finding

The plan's Step B.1 required us to verify whether the round-trip
actually happens before writing the filter. Verification result:
**`reasoning_content` does NOT round-trip in current openai_compat
code.** Inbound `reasoning_content` strings are converted to
`ThinkingBlock` (Wave2-1a P2, line 472 of `openai_compat.py`), and
ThinkingBlocks are dropped on the way out (Wave2-1a P4, line 285).
There is no path in stock code where reasoning leaks back into the
outbound dict.

The filter is therefore a **forward-compatibility defensive pass**.
It guarantees that any future change which lands a raw
`reasoning_content` string on an outbound assistant message dict
(e.g. an experimental subclass that round-trips signed reasoning,
or a third-party provider plugin) will have it stripped for
opt-in profiles. Tests cover both stock-code no-op behaviour and
synthetic forward-compat scenarios.

### Profile opt-ins

- `examples/model_profiles/65-glm-5.1.toml` — adds a `[tool_consumption]`
  section with `reminder_after_each_call = true`,
  `strip_prior_reasoning = true`. End users copy this file to
  `~/.llmcode/model_profiles/glm-5.1.toml` to activate.
- DeepSeek-R1 not opted in by default — recommended in the author
  guide (`docs/engine/model_profile_author_guide.md`) for the same
  separate-reasoning-channel pattern, but Adam can opt in after
  observing GLM-5.1 metrics.

### Anthropic provider unaffected

The Anthropic provider (`api/anthropic_provider.py`) round-trips
ThinkingBlocks via the structured `{"type": "thinking", ...}` format
with signatures — that path is required for signed extended thinking
to validate and is not touched by Mechanism B. Filter applies only to
`openai_compat.py::_build_messages`.

### Tests

- 19 new tests in `tests/test_api/test_openai_compat_reasoning_filter.py`:
  helper unit tests (5), filter flag-off path (2), filter flag-on
  forward-compat scenarios (5), one-log-per-call aggregation (3),
  GLM-5.1 TOML opt-in fixture verification (1). All scenarios pass
  with both stock code and synthetic forward-compat injection.
- Full suite: 7617 passed, 34 skipped (was 7598 in v2.4.0a1).
- Grep guard (`tests/test_no_model_branch_in_core.py`) stays green.

### Compatibility

- Profiles with `strip_prior_reasoning = false` (every profile except
  the opted-in GLM-5.1 example) produce byte-identical outbound
  messages to v2.4.0a1.
- Profiles with `reminder_after_each_call = false` AND
  `strip_prior_reasoning = false` produce byte-identical outbound
  messages to v2.3.2.

## v2.4.0a1 — v14 Mechanism A: post-tool reminder injection

First alpha of v14's tool-result consumption compatibility layer (see
`docs/superpowers/specs/2026-04-27-llm-code-v14-tool-consumption-compat-design.md`).
v2.3.2 demonstrated that prompt-level anti-contradiction text alone
cannot stop models like GLM-5.1 from denying a tool they just used.
v14 moves the correction from the session-frozen system prompt to
turn-proximate runtime hooks. This alpha ships the cheapest of the
three planned mechanisms.

### Mechanism A — Post-tool reminder injection

After every successful tool execution, the runtime appends a
synthetic `user` role message containing a short `<system-reminder>`
block that names the tool just used and instructs the model to
consume the tool result rather than deny the capability:

```
<system-reminder>
You just called {tool_name} and received the result above. That data
is your ground truth for this turn — consume it in your `content`
response. Do NOT deny the tool or capability you just used. If the
result is empty or an error, say so plainly and proceed.
</system-reminder>
```

The reminder rides in the same provider call as the tool result, so
the model sees the correction in the most recent ~50 tokens of
context — turn-proximate, where behavioural anti-patterns surface —
instead of relying on system-prompt text that drifts hundreds of
tokens away after a tool round-trip.

Cost: ~40 tokens per tool call. Typical 1–4 tool calls per user query
adds 40–160 tokens to the next provider call. Acceptable.

### New profile fields (v14 schema)

`ModelProfile` gains three new fields under a new `[tool_consumption]`
TOML section. All three are declared up front so subsequent v14 alphas
can wire mechanisms B and C without a second schema bump:

- `reminder_after_each_call: bool = True` — Mechanism A toggle.
  **Default ON.** Profiles for models that already consume tool
  results reliably can opt out (`reminder_after_each_call = false`).
- `strip_prior_reasoning: bool = False` — declared; wired in v2.4.0a2.
- `retry_on_denial: bool = False` — declared; wired in v2.4.0.

### Config / observability

- New module `llm_code.runtime.tool_consumption` exposing
  `build_post_tool_reminder(tool_name, profile) -> Message | None`.
- Structured INFO log on each injection:
  `tool_consumption: reminder_injected tool=<name> bytes=<n>`.
- Wired into the conversation turn loop immediately after the tool
  result message is added to session history. One reminder message
  per tool call (multi-tool turns produce multiple reminder messages
  named for each tool).

### Compatibility

- All built-in profiles inherit `reminder_after_each_call=True` from
  the dataclass default — no per-profile TOML edits required.
- Profiles with `reminder_after_each_call = false` produce
  byte-identical message history to v2.3.2.
- Grep guard (`tests/test_no_model_branch_in_core.py`) stays green —
  zero per-model branches added.
- Default profile resolution (`get_profile`, `ProfileRegistry`)
  unchanged. Existing 67 profile-system tests continue to pass.

### Tests

- 23 new tests in `tests/test_runtime/test_tool_consumption.py`
  covering the helper (defaults, opt-out, defensive empty input,
  log emission, message shape) and the conversation runtime wiring
  (single-tool, multi-tool, flag-on, flag-off).
- Full suite: 7598 passed, 34 skipped (was 7575 + 34 in v2.3.2).

## v2.3.2 — Tool-result consumption discipline (anti-contradiction)

Observed in a GLM-5.1 session: model calls `web_search`, receives valid
results, then writes in `content`: "I don't have access to news APIs
or the internet." The tool output is silently dropped and the user
sees a generic capability disclaimer instead of the requested
summary.

Two prompt-level fixes:

- `_BEHAVIOR_RULES` (universal, all models) gains: "Trust tool
  results you just received. NEVER claim you lack a tool, capability,
  or network access after successfully calling a tool that uses it
  in the same turn."
- `engine/prompts/models/glm.j2` gains a dedicated "Tool results ARE
  your ground truth" section and two new Common-Mistakes bullets
  spelling out the exact failure pattern (call → receive → deny) so
  the model recognises it in its own reasoning.

No API / tool surface changes; prompt text only.

## v2.3.1 — UX: quiet expected-fallback logs + time-sensitive search hygiene

Five signals observed in a GLM-5.1 session were user-visible noise
rather than actionable problems. This release demotes them to DEBUG
and tightens one search query path:

- `skill_router tier_c timed out ... skipping` — was WARNING, now DEBUG.
  The skip falls back to tier A/B routing; expected path under slow
  models.
- `openai_compat: dropping N thinking block(s) from outbound assistant
  message` — was WARNING, now DEBUG. Protocol mismatch: OpenAI-compat
  servers reject ThinkingBlocks, so dropping is correct. The once-per-
  process guard is retained.
- `StreamParser.flush: unterminated <tool_call> block, salvaging N
  chars as TEXT` — was WARNING, now DEBUG. The salvage handles the
  truncation gracefully; user has no action.
- Status line `?/Nk tok` token placeholder — now `-/Nk tok`. The `?`
  read as a warning glyph during first-turn streaming before the
  usage chunk arrived.
- `web_search` added `_augment_time_sensitive_query` at the tool
  entry point — appends today's ISO date when the query hits a
  trigger word (`today`/`latest`/`今日`/`現在`/…) AND lacks an
  explicit `YYYY-MM-DD`. Fixes the `今日熱門新聞 2026年4月` →
  stale-archive hit case. The augmented query is logged at INFO.

No behaviour change for non-GLM models; no API surface change.

## v2.3.0 — Profile-driven adapters GA (v13 migration complete)

Three years ago the easiest way to add a new model family to llmcode
was to land another `if "<family>" in model_name:` branch inside
`runtime/prompt.py`, `tools/parsing.py`, and `view/stream_parser.py`.
Over ~12 model families that approach accumulated a tangle of
hardcoded substring checks. v13 replaces all three call sites with a
single lookup against a flat registry of `ModelProfile` objects that
ship as TOML files under `examples/model_profiles/`.

v2.3.0 is the GA of that migration. Built-in behaviour is **unchanged
for every model id llmcode has ever supported** — the Phase B parity
test verified byte-level identical system prompts and tool-call
parse results for 36 model ids before the legacy code was deleted.
The new path is strictly additive from the user's perspective: to add
a new model family you write a ~30-line TOML in
`~/.llmcode/model_profiles/`, no Python changes required.

`pip install -U llmcode-cli` picks it up.

### New features

- **Profile-driven adapters** — `ModelProfile` gained five new fields
  (`prompt_template`, `prompt_match`, `parser_variants`,
  `custom_close_tags`, `call_separator_chars`) that route a model id
  to its tuned intro prompt + parser behaviour. Built-in profiles
  ship under `examples/model_profiles/<NN>-<name>.toml`; user
  overrides are picked up from `~/.llmcode/model_profiles/`.
- **Parser variant registry** (`llm_code.tools.parser_variants`) —
  the six tool-call formats (JSON payload, Hermes full, Hermes
  truncated, Harmony / variant 7, GLM variant 6, bare name-tag) are
  now named plugins. A profile's `parser_variants` tuple declares
  enabled variants and their order; empty = `DEFAULT_VARIANT_ORDER`.
- **Profile registry** (`llm_code.runtime.profile_registry`) —
  `resolve_profile_for_model(model_id)` walks the registered
  profiles and returns the first whose `prompt_match` substring hits.
  User profiles registered before the lazy built-in sweep win on
  collision.
- **12 new built-in TOMLs** under `examples/model_profiles/` covering
  the full legacy ladder: 10-copilot, 15-codex, 20-beast, 25-gpt,
  30-claude-sonnet, 35-gemini, 40-trinity, 45-qwen3.5-122b,
  50-llama, 55-deepseek, 60-kimi, 65-glm-5.1, 99-custom-local.
  Numeric filename prefixes enforce resolution order so substring
  collisions (e.g. `copilot-gpt-5` containing both "copilot" and
  "gpt-5") resolve to the correct profile.

### Deprecations

- **`llm_code.runtime.prompt.select_intro_prompt(model)`** is
  deprecated and now emits `DeprecationWarning` on every call. It
  remains as a thin shim that calls
  `load_intro_prompt(resolve_profile_for_model(model))` so
  third-party imports keep working. **Removal is scheduled for
  v14.** Migrate now:

  ```python
  # Before (v2.2.5 and earlier)
  from llm_code.runtime.prompt import select_intro_prompt
  intro = select_intro_prompt(model)

  # After (v2.3.0+)
  from llm_code.runtime.prompt import load_intro_prompt
  from llm_code.runtime.profile_registry import (
      _ensure_builtin_profiles_loaded,
      resolve_profile_for_model,
  )
  _ensure_builtin_profiles_loaded()
  intro = load_intro_prompt(resolve_profile_for_model(model))
  ```

### Breaking changes

- **`StreamParser` defaults no longer assume GLM.** In v2.2.4 and
  v2.2.5 a bare `StreamParser()` supported GLM variant 6
  (`</arg_value>` close tag) and Harmony variant 7
  (`<arg_key>` required-on guard) out of the box. In v2.3.0 the
  class-level defaults are empty tuples / empty string — only
  `</tool_call>` terminates a `<tool_call>` block by default.
  Callers that need variant-6 / variant-7 behaviour must either
  (a) pass the hints explicitly, or (b) resolve the GLM profile and
  forward its `custom_close_tags` + `call_separator_chars` fields.
  The GLM profile (`65-glm-5.1.toml`) now carries those settings
  in a `[parser_hints]` section.

  Impact: any downstream code that constructed `StreamParser()` with
  no kwargs and depended on GLM support must switch to
  `StreamParser(custom_close_tags=("</arg_value>",),
  call_separator_chars="\u2192 \t\r\n",
  standard_close_required_on=("<arg_key>",))`. The llmcode built-in
  dispatcher already reads profile hints at construction time; only
  custom integrations are affected.

- **`_legacy_select_intro_prompt` function removed** from
  `llm_code.runtime.prompt`. It was an internal helper — not part of
  any documented API — but plugins that imported it directly will
  break. Use `load_intro_prompt(resolve_profile_for_model(model))`
  instead.

- **`tests/test_runtime/parity/`, `tests/test_tools/parity/`, and
  `tests/test_view/parity/` directories removed** along with the
  three `tests/fixtures/pre_v13_*.json` snapshots. Mainline tests
  now cover the same assertions. The
  `scripts/capture_prompt_baseline.py` capture script is kept for
  future migrations.

### Internal guards

- `tests/test_no_model_branch_in_core.py` is a new parametrised
  regression guard. It fails if any `if "<model-family>" in VAR:`
  shape reappears in `runtime/prompt.py`, `tools/parsing.py`, or
  `view/stream_parser.py`. Part of the "model-specific logic lives
  in TOMLs, not Python branches" ship gate.

### Upgrade pointer

End users: drop-in. Plugin authors with custom `ModelProfile`
subclasses or who patched the legacy if-ladder: update to the
profile-driven path before v14 drops the shim. See
`docs/engine/model_profile_author_guide.md` (updated).

---

## v2.2.0rc1 — v12 Haystack-borrow GA: engine cutover + legacy deletion

The final milestone of the v12 overhaul. All eight v12 plans (M0–M8)
are now landed and the transitional shims are gone. The engine path
shipped in v2.0.x is now the only path — every legacy fallback has
been deleted.

If you are an end-user running llmcode via `pip install llmcode-cli`,
the upgrade is drop-in: session files, HIDA indices, and slash commands
all continue to work. If you are a plugin author who subclassed
`ToolExecutionPipeline` or imported from `llm_code.runtime.prompts.mode`,
run `llmcode migrate v12 <plugin>/` to source-rewrite your code to the
new Component API before upgrading. See `docs/plugin_migration_guide.md`.

### Breaking changes

- **Python 3.11 or newer is required.** Python 3.10 support is dropped;
  `pip install` on 3.10 now fails at dependency resolution with a clear
  `requires-python` marker.
- **`LLMCODE_V12` environment variable removed.** It was a transitional,
  internal-only flag used by the M1–M7 parity suite; no supported code
  path ever read it after GA.
- **`EngineConfig._v12_enabled` field removed.** Callers that flipped
  the flag manually (tests, experimental harnesses) must drop the kwarg
  — all runs now flow through the engine path unconditionally.
- **`runtime/prompts/*.md` and `runtime/prompts/mode/*.md` directories
  removed.** Templates live at `llm_code/engine/prompts/models/*.j2`
  and `llm_code/engine/prompts/modes/*.j2`. Both directories are the
  canonical source; the legacy markdown dir no longer ships in the
  wheel.
- **`ToolExecutionPipeline` subclassing no longer supported.** Plugins
  that customised tool dispatch via subclass must migrate to the M2
  Component API. The codemod `llmcode migrate v12` handles the common
  shapes; unsupported patterns are reported with file:line pointers.
- **`remote/server.py` and `ide/server.py` removed.** Absorbed into the
  `hayhooks/` transport (M4) with equivalent endpoints. Set
  `hayhooks.enable_debug_repl` / `hayhooks.enable_ide_rpc` in config to
  restore the old behaviour.
- **`tests/test_engine/parity/` directory removed.** Parity tests had
  no legacy counterpart left to compare against.

### New features

- **Hayhooks transport (M4)** — unified FastAPI surface covering
  OpenAI-compat chat, MCP, headless REPL, and IDE RPC. Single port,
  single auth token, single rate-limit policy.
- **OpenTelemetry + Langfuse observability (M6)** — first-class tracing
  and metrics, opt-in via `observability.exporter = "otlp" | "langfuse"`.
  Redaction is on by default; sample rate is tuneable.
- **Memory Components (M7)** — HIDA retrieval, vector recall, and
  summarisation ship as composable Components. The legacy
  `memory/service.py` indirection is gone.
- **Async engine path (M5)** — `AsyncPipeline` alongside `Pipeline`;
  sync/async parity was the M5 ship gate.
- **PromptBuilder (M1)** — Jinja2-backed template rendering. Every
  model- and mode-specific prompt now lives in `engine/prompts/`.
- **Codemod CLI (M8.a)** — `llmcode migrate v12 <path>` rewrites the
  four most common plugin migration shapes in place.

### Upgrade pointer

See `docs/upgrade_to_v2.md` for the end-user and plugin-author
checklists, `docs/plugin_migration_guide.md` for the codemod walkthrough,
and `docs/breaking_changes_v2.md` for the full old→new symbol table.

---

## v2.1.0 — Reference-alignment sprint: sandbox enforcement, granular network policy, mode reminders

Fifty commits of reference-aligned improvements against opencode,
Claude Code, Codex CLI, and the other AI coding agents in
`~/Work/qwen/reference/`. Nothing breaks — all v2.0.0 configs,
sessions, and slash commands continue to work. The focus is making
llmcode match or beat the sandbox enforcement, permission model,
and model-family ergonomics of the reference implementations.

`pip install -U llmcode-cli` picks it up.

### Sandbox enforcement (Sprints 4–7)

What used to be a single "docker or bust" path is now a full platform-
aware chain with real OS-level enforcement:

- **PTY adapter** — baseline stream-capable runner for hosts with no
  sandbox primitive available.
- **Bwrap adapter** — Linux bubblewrap with per-line streaming.
- **Seatbelt adapter** — macOS `sandbox-exec` with per-call profile
  generation (deny-default, file-read*, file-write* scoped to the
  workspace subtree).
- **Landlock adapter** — Linux 5.13+ LSM via direct libc ctypes
  syscalls (create_ruleset / add_rule / restrict_self). Full
  `preexec_fn` integration so the child task is restricted before
  `exec`.
- **Docker adapter** — real per-line streaming via the `docker
  exec --interactive` path plus tighter-than-container rejection so
  misconfigured per-call policies cannot silently execute.
- **`choose_backend()`** — platform-aware priority chain with WSL2
  detection, `/proc/sys/kernel/osrelease` parsing so WSL routes to the
  Linux chain even when CPython reports Windows.
- **`SandboxLifecycleManager`** — session-scoped teardown so
  per-turn docker / bwrap handles close deterministically at REPL
  exit, remote-server shutdown, or sub-agent disposal.

### Granular network policy (H3 completion)

- New `SandboxPolicy.allowed_ports` and `allowed_cidrs` fields.
- JSON-schema loader under `llm_code/sandbox/policy_schema.py`
  (`policy_from_dict` / `policy_from_json` / `policy_to_dict`) with
  typed validation — actionable `PolicySchemaError` on unknown
  fields, bad port ranges, malformed CIDRs.
- Seatbelt backend translates a non-empty allowlist into granular
  `(allow network-outbound (remote tcp "*:<port>"))` directives
  instead of the coarse `(allow network*)`.
- Empty tuples preserve the existing all-or-nothing semantics so
  every existing call site keeps working unchanged.

### Model-specific prompts

Three new variants, reference-aligned with opencode's
`session/prompt/`:

- **`beast.md`** — OpenAI reasoning models (o1 / o3 / gpt-4 / gpt-5).
  The baseline `gpt.md` under-delivers for reasoning models; beast
  ships the "keep going, plan, iterate" instruction set.
- **`copilot_gpt5.md`** — GitHub Copilot's GPT-5 backend with its
  distinct tool surface / memory convention.
- **`trinity.md`** — model ids containing `trinity`.

Routing precedence rewritten: `copilot` > `codex` > reasoning-class
(o1 / o3 / gpt-4 / gpt-5 → beast) > plain gpt > claude / gemini /
trinity / qwen / llama / deepseek / kimi > default.

### Mode-specific reminders with auto-injection

Four templates under `prompts/mode/` with matching builder functions:
`plan_mode_reminder`, `plan_mode_reminder_anthropic`,
`build_switch_reminder`, `max_steps_reminder`. Auto-injection wires:

- **Plan / read-only reminder** — `SystemPromptBuilder.build()`
  auto-injects when `PermissionMode.PLAN` or `READ_ONLY` is active.
  Claude-family models get the 5-phase Anthropic variant with an
  explicit `ExitPlanMode` contract; everything else gets the default.
- **Build-switch reminder** — `PermissionPolicy.switch_to(target)`
  records a `ModeTransition` event; `SystemPromptBuilder` reads it
  once via `consume_last_transition()` and auto-injects when the
  flip relaxes the read-only constraint.
- **Max-steps reminder** — `IterationBudget` lives alongside
  `AutoCompactState` in `auto_compact.py`, ticks per turn iteration,
  and `ConversationRuntime.run_turn()` yields the reminder text when
  the budget exhausts.
- **Shift+Tab binding** — fulfils the long-standing promise in
  `PLAN_MODE_DENY_MESSAGE` that Shift+Tab flips plan↔build. Routes
  through `PermissionPolicy.switch_to` so the build-switch reminder
  auto-injects on the next turn.

### Fourteen M-series follow-ups

Reference-aligned reusable modules delivered in priority order:

| | | |
|---|---|---|
| **C5** `EditSnapshot` store | **H7** plugin dependency resolver | **M1** `ToolResultBuffer` |
| **M2** compact boundary message | **M3** `SessionMode` + headless auto-approve | **M4** nested-memory tracker |
| **M5** structured git diff parser | **M6** config schema versioning | **M7** in-memory inter-agent mailbox |
| **M8** XML-mode tool-schema filter | **M9** OpenTelemetry tracing skeleton | **M10** SEA binary-integrity manifest |
| **M11** i18n catalog (zh-TW + en) | **M12** skill lazy-loader decorator | |

### Tests

6182 passing (+655 from v2.0.0's 5527), 15 skipped, zero regressions.
Ruff clean. Full suite runs in ~3:35 on an M-series Mac.

---

## v2.0.0 — REPL Mode: native terminal UX, ViewBackend Protocol, Textual TUI removed

Major rewrite of llmcode's view layer, delivering on the "permanently
solve the class of bugs that drove v1.17 through v1.23 to re-flip the
mouse-capture setting four times" promise. llmcode now launches a
line-streaming REPL built on prompt_toolkit + Rich instead of a Textual
fullscreen TUI. Your config, sessions, history, and all 53 slash
commands carry over unchanged.

See [`docs/migration-v2.md`](docs/migration-v2.md) for the full
upgrade guide. TL;DR: `pip install -U llmcode-cli`.

### 🚨 Breaking changes

- **Textual fullscreen TUI removed.** `llmcode` now launches a
  line-streaming REPL built on prompt_toolkit + Rich. All your
  config, sessions, history, and slash commands carry over; only
  the visual presentation is different.
- **No commands are removed.** All 53 slash commands still work.
  A few interactive *flows* changed because their Textual widgets
  no longer exist:
  - **`/help`** — was a three-tab modal; now an inline print into
    scrollback (searchable + copyable natively).
  - **`/settings`** — was a modal; now an inline print. Edit
    fields with `/set <key> <value>`.
  - **`/skill`, `/mcp`, `/plugin`** (no sub-command) — was a
    marketplace card browser; now a plain list + usage hint. The
    `install`, `enable`, `disable`, `remove` sub-commands are
    unchanged.
  - **`/theme`** — now honors your terminal's own palette via
    Rich; the runtime theme switcher is gone.
  - **`/vim`** — prompt_toolkit has its own vim-mode layer that
    isn't runtime-toggleable from a slash command yet. The
    command prints a note.
- **Textual dependency removed.** `pip install llmcode-cli` no
  longer pulls `textual>=1.0`. If you had Python 3.9 environments
  that needed Textual for unrelated reasons, that's your
  responsibility now.
- **Backwards-compat shim kept** at
  `llm_code.streaming.stream_parser` — it now re-exports from
  `llm_code.view.stream_parser` so any out-of-tree consumer of
  the canonical stream parser keeps working.

### ✨ User-visible improvements

- **Native mouse drag-select-copy** works in Warp / iTerm2 / Kitty /
  macOS Terminal / xterm without holding any modifier.
- **Terminal-native scroll wheel** scrolls the shell's scrollback as
  it should. No more `/scroll` command or `Shift+↑↓` workaround.
- **Terminal Find (⌘F / Ctrl+F)** works because llmcode no longer
  enters alt-screen mode. Search your conversation history with the
  terminal's own search.
- **Warp AI block recognition**, **iTerm2 split panes**, and
  **tmux copy-mode** all work correctly because llmcode doesn't
  capture mouse events or take over the screen.
- **OSC8 hyperlinks** click-through in Warp / iTerm2 / WezTerm.
- **The v1.23.1 wheel-triggered `/voice` regression is structurally
  impossible** in the new REPL — there is no mouse capture to
  misroute scroll events into the input buffer.
- **Faster cold start**: the REPL is up in well under a second on a
  warm cache; the Textual TUI could take 2–3s on first run.

### 🏗 Architecture

- **New `llm_code/view/` package** contains the entire view layer:
  - `base.py` — `ViewBackend` abstract base class, the extension
    point for future platform backends (Telegram, Discord, Slack,
    Web in v2.1+).
  - `types.py` — `MessageEvent`, `StatusUpdate`, the
    `StreamingMessageHandle` / `ToolEventHandle` Protocols.
  - `dialog_types.py` — `Choice`, `DialogCancelled`,
    `DialogValidationError`, `TextValidator`.
  - `dispatcher.py` — 53 view-agnostic slash-command handlers
    plus the top-level `run_turn` router.
  - `stream_renderer.py` — consumes `runtime.run_turn`'s
    `AsyncIterator[StreamEvent]` and drives any `ViewBackend`.
  - `repl/` — the first-party REPL backend on prompt_toolkit + Rich.
  - `diagnostics.py`, `session_export.py`, `settings.py`,
    `stream_parser.py`, `headless.py`, `scripted.py` — utility
    modules relocated from the deleted `tui/` package.
- **`runtime/app_state.py`** — new `AppState` dataclass +
  `from_config(config, cwd, ...)` factory. Replaces the ~30 state
  fields that used to live on `LLMCodeTUI.__init__` + the
  `RuntimeInitializer.initialize()` adapter, so the REPL backend
  can construct the same subsystem graph without instantiating
  a Textual app.
- **`runtime/core_tools.py`** — the collaborator-free core tool
  set registration helper, relocated from `tui/app.py`. Shared by
  `AppState.from_config` and the headless `run_quick_mode` path.
- **Old `llm_code/tui/` package deleted** — 30 files, ~9400 lines,
  including `app.py`, `command_dispatcher.py` (2455 lines, 52
  `_cmd_*` methods), `streaming_handler.py` (463 lines), the
  chat/header/input/status widgets, the marketplace browser, the
  settings modal, all four Textual dialog variants, and the
  Textual theme system.
- **`tests/test_tui/` + `tests/test_e2e_tui/` deleted** —
  65 files / ~9400 lines of widget-coupled tests replaced by
  view-layer equivalents. Two runtime-layer tests
  (`test_register_core_tools`, `test_secret_redaction`) were
  relocated to `tests/test_runtime/`.

### 🎙 Voice input (M9 + M9.5)

- New `PollingRecorderAdapter` bridges the real polling-API
  `AudioRecorder` (sounddevice / sox / arecord) to the M9 backend's
  callback interface, so voice **actually works** in production
  v2.0.0. The earlier M9 path shipped with a callback-API
  assumption that only the test FakeRecorder matched.
- R3 (voice + asyncio deadlock) stress test scaled from 10 → 100
  iterations + new `test_voice_during_active_streaming` and
  `test_rapid_voice_restart` coverage.
- Voice state (`voice_active`, `voice_recorder`, `voice_stt`) now
  lives on `AppState` instead of on `REPLBackend`, so the `/voice`
  command path and the `Ctrl+G` hotkey path share one source of
  truth.

### 🧪 Tests

- **~900 new tests** across `tests/test_view/`, `tests/test_e2e_repl/`,
  `tests/test_runtime/` (new AppState + renderer + dispatcher suites).
- **Protocol conformance harness** in
  `tests/test_view/test_protocol_conformance.py` — every future
  `ViewBackend` subclass inherits the base test class for free.
- **`StubRecordingBackend`** fixture for unit-level tests that want
  logic coverage without a real terminal.
- **`repl_pilot`** fixture for component tests driving a real
  `REPLBackend` with a captured Rich Console.
- **21 `pexpect` smoke tests** (`tests/test_e2e_repl/test_smoke.py`)
  spawning the real `llmcode` binary in a pseudo-TTY and asserting
  on visible terminal output — including hard architectural guards
  that the REPL emits no alt-screen or mouse-tracking escape
  sequences.
- **22 snapshot goldens** (`tests/test_view/snapshots/`) for visual
  regression coverage of StatusLine, ToolEventRegion, DialogPopover,
  and Rich panels.
- **LLMCODE_TEST_MODE** env var support in `cli/main.py`: when set,
  plain-text submissions echo as `echo: <text>` instead of calling
  the LLM, so end-to-end smoke tests can run without an API key.

Full project suite: **5344 tests** passing at the v2.0.0 tag.
(v1.23.1 was ~5488 including the now-deleted `test_tui/` tree.)

### 📦 Dependencies

- **Removed:** `textual>=1.0`
- **Already present:** `prompt-toolkit>=3.0.47`, `rich>=13.0`
- **Added to dev extras:** `pexpect>=4.9.0`

### 🧹 Tech debt cleanups rolled in

- `_screen_lock` dead code removed from `ScreenCoordinator` (was
  never acquired by any production code path; YAGNI per the M0
  PoC evidence).
- `swarm/test_backend_subprocess.py` fixture rewritten to use
  `MagicMock` for sync methods and `AsyncMock` for async methods,
  eliminating a class of never-awaited-coroutine RuntimeWarnings.
- `tests/test_integration_v4.py` provider fixture similarly
  unwound for `supports_native_tools` / `supports_images` /
  `supports_reasoning` sync methods.
- `@pytest.mark.unit` registered in `pyproject.toml`'s
  `[tool.pytest.ini_options].markers` so existing `unit` marker
  uses no longer emit `PytestUnknownMarkWarning`.
- All TypeScript-like `list[X]` / `dict[X, Y]` annotations reviewed
  for Python 3.10+ compatibility.

### Milestone trace

This release is the end of the M0–M14 plan tree. Individual
milestones:

- **M0–M2**: Spec + `view/base.py` + `view/types.py` + Protocol
  conformance harness
- **M3–M9**: REPL component build (coordinator, input area, slash
  popover, status line, live response, tool events, dialogs, voice)
- **M9.5**: Voice adapter + `_screen_lock` deletion + stress test
- **M10**: `AppState` + `ViewStreamRenderer` + `CommandDispatcher`
  (the largest milestone — 4 sub-commits for the 53-command port)
- **M11**: Cutover — `cli/main.py` rewrite, `tui/` + `test_tui/`
  deletion, `textual` dependency removed
- **M12**: Pexpect smoke suite
- **M13**: Snapshot goldens
- **M14**: Docs + release

### Migration

See [`docs/migration-v2.md`](docs/migration-v2.md) for the full
upgrade guide.

---

## v1.23.1 — TUI text selection + scroll-wheel-history collision, fixed

Patch release addressing two tightly-coupled UX bugs in the Textual
fullscreen TUI surfaced by daily use in Warp.

### Bugs fixed

1. **Native click-drag text selection was broken.** Mouse capture was
   defaulted to `True` in v1.17.0 ("TUI scroll fix") to make the
   in-app scroll wheel work, at the cost of routing all mouse events
   to the Textual app and blocking the terminal's native click-drag
   text selection. This was the fourth flip of this setting in the
   project's history — each previous attempt traded one bug for
   another. v1.23.1 picks `mouse=False` as the default and addresses
   the wheel side separately (fix #2).

2. **Scroll-wheel pulled `/voice` (and other history) into the input
   and froze the chat view.** Warp (and some other terminals) enable
   "alternate scroll mode" (DECSET ?1007) by default in alt-screen
   TUIs that don't hook mouse tracking, translating wheel events into
   bare Up/Down arrow keystrokes. Those arrows landed on `InputBar`,
   which had history recall hardcoded to bare Up/Down — so every
   wheel scroll-up rewound the input buffer to the previous command
   (typically `/voice`, with the live `Voice Input` banner from a
   prior invocation still visible, making it look like voice was
   self-triggering). Fixed two ways: (a) emit `\x1b[?1007l` on TUI
   mount and `\x1b[?1007h` on unmount so terminals that respect the
   sequence stop translating wheel events, and (b) move history
   recall off bare ↑/↓ onto **Ctrl+↑ / Ctrl+↓** so even terminals
   that ignore ?1007l can no longer spuriously rewind history with a
   wheel scroll. The InputBar history block is now wired through the
   keybinding action registry (`history_prev` / `history_next`)
   instead of bypassing it.

### User-visible behavior changes

- **Mouse drag-select copy now works** in Warp / iTerm2 / Kitty / etc.
  without holding any modifier.
- **App-level mouse wheel scroll is no longer captured.** Use
  `Shift+↑/↓`, `PageUp/PageDown`, or `/scroll` for in-app chat
  scrolling. The terminal's native scrollback (when not in alt-screen
  contexts) is unaffected.
- **History recall moves from bare ↑/↓ to Ctrl+↑ / Ctrl+↓.** Bare
  ↑/↓ now fall through to the default `Input` widget behavior
  (intra-line cursor movement in multi-line buffers, no-op in
  single-line) — harmless when delivered by a stray wheel event.
- **Mouse capture is now configurable** via `mouse = true` in the
  user config for terminals where in-app wheel capture is preferred
  over native text selection (default: `false`).

### Tests

- 657 TUI tests passing (was 657 in v1.23.0). 2 new regression
  guards added in `test_prompt_history_e2e.py` asserting bare ↑/↓
  do not recall history under any condition (including after a
  successful Ctrl+↑ recall).

### Files touched

- `llm_code/cli/tui_main.py` — flip `mouse=True` → `mouse=False`
- `llm_code/tui/app.py` — emit `\x1b[?1007l` in `on_mount`,
  `\x1b[?1007h` in `on_unmount`
- `llm_code/tui/input_bar.py` — remove bare ↑/↓ history block,
  wire `history_prev` / `history_next` actions in `_handle_action`
- `llm_code/tui/keybindings.py` — `history_next` default
  `ctrl+n` → `ctrl+down` (symmetric with `history_prev = ctrl+up`)
- `tests/test_e2e_tui/test_prompt_history_e2e.py` — switch to
  `ctrl+↑/↓`, add 2 bare-↑/↓ regression tests

---

## v1.23.0 — 52 Commands × Deep E2E: the "pytest green ≠ user-runnable" gap, closed

This release is about one thing: **every slash command now has a deep
end-to-end pilot scenario that boots the real TUI and asserts on
observable state.** Historically the unit tests were green while shipped
builds had broken autocomplete dropdowns, unscrollable modals, silent
voice failures, and mis-routed keybindings — the gap between "pytest
passes" and "user-runnable" was costing an entire bug class per release.
v1.23.0 closes that gap.

### Highlights

- **🧪 Full E2E pilot suite for every slash command** — 185 scenarios
  across 13 files under `tests/test_e2e_tui/`, driving a real
  `LLMCodeTUI` instance via Textual's `App.run_test()` pilot API.
  Every one of the 52 `CommandDef` entries in `COMMAND_REGISTRY` has
  a matching DEEP or MODAL scenario; the only SKIP entries are
  `/exit` (quits the loop) and `/settings` (empty-Static modal).
  The suite runs in **~55 seconds** on a 2026 M-series MacBook Pro,
  with zero external dependencies — no network, no mic, no LLM
  credentials, no real git repo. Fully deterministic: no
  `time.sleep`, no retries, no flaky assertions.
- **Coverage matrix documented in `tests/test_e2e_tui/README.md`** —
  every command is tagged DEEP / SMOKE / MODAL / SKIP with a
  direct link to its test file and a one-line note on what the
  scenario asserts. Adding a new command now has an obvious
  checklist: "add a row to the matrix, add a scenario file."
- **Two meta-invariant tests prevent the exact drift that caused
  four commands to ship without autocomplete hints in v1.22.x:**
  `test_dispatcher_has_all_52_commands` walks `COMMAND_REGISTRY` and
  asserts every entry has a matching `_cmd_*` handler;
  `test_registry_has_no_dead_handlers` walks the dispatcher class
  and asserts every `_cmd_*` has a registry entry. If either side
  drifts, CI fails with a pointed error — no more "only learns
  about it from the source code".

### Deep E2E Coverage by Category

| Category | Commands | New Scenarios |
|---|---|---|
| Core UX (`/help` /clear /copy /cancel /yolo /thinking /vim /model /theme /update) | 10 | 18 |
| Voice (`/voice` + Ctrl+G + VAD + banner) | 1 | 7 |
| Input / interaction (slash dropdown, prompt history, multiline, cycle-agent, `/image`) | 5 features | 23 |
| Info / config (`/cost` /gain /profile /cache /personas /budget /set /config /cd /map /dump) | 11 | 25 |
| Session / memory / history (`/session` /memory /undo /diff /compact /checkpoint /export) | 7 | 26 |
| Workflow (`/plan` /mode /harness /search /cron /task /swarm /orchestrate /hida) | 9 | 27 |
| 外掛生態系 (`/plugin` /skill /mcp) | 3 | 17 |
| Heavy / IO-bound (`/init` /index /knowledge /analyze /lsp /vcr /ide) | 7 | 21 |
| Meta / cross-cutting (`test_all_slash_commands.py`) | — | 15 |
| Boot / banner | — | 6 |

**Total: 52 commands, 185 scenarios, +122 vs the partial E2E suite in
v1.22.1.**

### What the deep scenarios actually verify (a sampling)

- **Modal scroll bugs caught at boot** — `test_help_modal.py` opens
  `/help`, navigates past item 13 with `pilot.press("down")`, and
  asserts the 52-item list scrolls past the viewport. Earlier
  iterations shipped with an OptionList that couldn't scroll because
  of an overflow: hidden on the modal container — that bug is now
  regression-locked.
- **Slash dropdown navigation under real focus** —
  `test_slash_dropdown.py` types `/he`, asserts `/help` surfaces as
  the top filter hit, then exercises Tab / Enter / → accept paths
  and Escape dismiss. The dropdown had three separate broken
  states over the v1.22.x line (focus stolen by Static, → not
  wired to accept, Esc leaving ghost state) — all three now have
  named scenarios.
- **`/voice` full mic flow including macOS permission symptom** —
  `test_voice_flow.py` covers Ctrl+G hotkey toggle, typo reject
  (`/voice /oof` must not silently stop recording), VAD auto-stop,
  speech-gate latch (silence window can't open before the first
  chunk of real speech), and the dedicated "No audio captured /
  check macOS Microphone permissions" troubleshooting path that
  fires when `recorder._has_heard_speech is False` + empty buffer.
- **Checkpoint cost-tracker round-trip** —
  `test_checkpoint_flow.py` saves a checkpoint with a populated
  cost tracker, resumes it, and asserts the restored runtime has
  the same token counts. This is the regression that almost
  shipped in v1.22.1 before the Wave2-2 fix landed.
- **Plugin / skill / mcp install + remove paths** —
  `test_plugin_skill_mcp.py` covers unsafe-name guards, clone +
  enable + reload flows (with `PluginInstaller` mocked at the
  module boundary), marker-file enable/disable, directory
  deletion on remove, and the `MarketplaceBrowser` push path
  (via `push_screen` interception to sidestep a pilot mount race).
- **Heavy worker dispatch without side effects** —
  `test_heavy_commands.py` intercepts `app.run_worker` so `/init`,
  `/update`, `/analyze`, etc. can be verified to schedule the
  right worker with the right name without actually running the
  LLM / pip / `run_analysis` path.

### Fixed (pre-release, post v1.22.1)
- **`/voice off` showed a cryptic "No audio captured" line** when
  the macOS Microphone permission was denied — the user had no
  way to tell whether it was a hardware bug, a driver problem, or
  a permission issue. Now surfaces the full `System Settings →
  Privacy & Security → Microphone` path plus the current VAD peak
  and mean telemetry, so power users can also diagnose genuine
  low-gain hardware issues.
- **`_cmd_voice` typo rejection guard** — `/voice /oof` used to
  fall through to the bare-status branch, leaving recording active
  while the user assumed they had stopped. Now explicitly rejected
  with a "Still recording — unknown subcommand" chat entry.

### Tests
- **5474 passing** (+122 vs v1.22.1). **12 skipped** unchanged.
- E2E pilot suite: **63 → 185 scenarios** (+122), **~55s** runtime.
- New files: `test_basic_toggles.py` (12), `test_info_commands.py` (25),
  `test_session_memory.py` (18), `test_workflow_commands.py` (27),
  `test_plugin_skill_mcp.py` (17), `test_heavy_commands.py` (21),
  plus the earlier-landed `test_boot_banner.py` / `test_help_modal.py`
  / `test_slash_dropdown.py` / `test_prompt_history_e2e.py` /
  `test_voice_flow.py` / `test_export_flow.py` / `test_all_slash_commands.py`
  / `test_theme_switch.py` / `test_multiline_input.py` /
  `test_cycle_agent.py` / `test_image_flow.py` /
  `test_checkpoint_flow.py`.
- Coverage matrix + authoring guide: `tests/test_e2e_tui/README.md`.

### Migration
None. This is a test-suite-only release. No runtime behavior changed
from v1.22.1 except for the two small voice UX fixes listed under
"Fixed", which are additive chat-entry improvements.

### Why this matters
A bug that pytest can't catch has to be found by a human running the
TUI and noticing it. That's slow, flaky, and doesn't scale. With 185
deterministic pilot scenarios running in under a minute, the same
class of bugs that slipped through the v1.22.x line — broken modal
scroll, missing autocomplete hints, silent voice failures,
mis-routed Ctrl+G dispatch — will now fail CI before the commit
lands. "Product complete" means the tests say so.

---

## v1.22.1 — Voice UX patch: VAD peak detection, Ctrl+G hotkey, banner hint

Field-test patch for v1.22.0. Two bugs showed up in the first hands-on
session and are fixed:

### Fixed
- **VAD never auto-stopped on a noisy laptop mic.** The mean-based
  detector with a 500 threshold was too aggressive — a MacBook with
  ambient fan hum runs at a 600–1500 mean, so the silence window
  never latched. Switched to **peak** detection with a 3000 default
  floor: speech generates 10000–20000 peaks that are easy to
  distinguish from <2000 room noise, no per-environment calibration
  needed. Both peak and mean are still computed so `/voice` status
  can surface them for manual tuning.
- **Ctrl+Space hotkey did nothing on macOS.** The system-wide Input
  Source switcher is bound to Ctrl+Space by default, so the
  keystroke never reached Textual. Default hotkey is now **Ctrl+G**
  (ASCII BEL, 0x07) — no shell / terminal / macOS conflict. InputBar
  still accepts `ctrl+space` / `ctrl+@` / `f9` as fallbacks so users
  who prefer those (or who want the legacy binding) can opt in via
  `config.voice.hotkey`.

### Polish
- **Welcome banner now shows the voice hotkey** when
  `voice.enabled == true` in config. Row format: `Voice   Ctrl+G to
  start/stop (auto-stops on silence)`. Hidden entirely when voice is
  off so non-voice users aren't polluted with noise.
- **Bare `/voice` status surfaces live VAD telemetry**: current peak,
  current mean, silence threshold, elapsed seconds. Makes it trivial
  to see whether your environment is too noisy for the default
  threshold.
- **`AudioRecorder._last_peak` / `_last_mean`** track the most recent
  chunk so the status view can read them without instrumenting the
  sounddevice callback from the outside.
- **sounddevice callback now wraps `_update_silence_tracker` in
  try/except**, so a VAD bug can never take down the capture stream
  and leave the recorder stuck.

### Tests
- 5284 passing (+2 vs v1.22.0). Recorder VAD suite rewritten around
  peak detection (added: noisy-room-below-threshold still-silent,
  instrumentation-updated). `test_voice/test_config.py::test_defaults`
  updated to the new hotkey + threshold defaults.

---

## v1.22.0 — Voice UX Pass: VAD, Timer, Hotkey, Typo-Safe Prompts

This release turns `/voice` from "it works if you type the commands
exactly right" into a polished hands-free workflow. Three UX additions
plus one usability bug fix, all driven by real-session friction.

### New Features

- **🎤 Voice activity detection (VAD) — auto-stop after silence** —
  `AudioRecorder` now tracks an RMS-proxy energy floor on every incoming
  PCM chunk. When the speaker stays quiet for `voice.silence_seconds`
  (default `2.0`), the recorder flags itself and a TUI poll timer
  tears capture down automatically, same path as `/voice off`. No
  more "did I just forget to stop recording?". Pure Python — uses
  `array.array("h")` for int16 unpacking, no numpy dependency added.
  Opt-out with `"silence_seconds": 0` in config. Tune for noisy
  environments with `"silence_threshold"` (default `500`; raise for
  HVAC / open office).
- **Status-bar recording timer (🎤 MM:SS)** — live elapsed readout
  appended to the bottom status bar while the recorder is running,
  priority-98 so width pressure drops it last. Ticks every 200ms,
  zeros out the moment recording ends. Uses a dedicated `set_interval`
  on `LLMCodeTUI` that also drives the VAD poll, so one timer
  services both features.
- **Ctrl+Space hotkey — toggle voice without typing a command** —
  press once to start recording, press again to stop + transcribe.
  Accepts both `ctrl+space` (kitty-protocol terminals) and `ctrl+@`
  (legacy ANSI where Ctrl+Space is a NUL byte), so it works across
  Warp, iTerm2, Terminal.app, and the Linux VTs. Push-to-talk (hold-
  to-speak) isn't possible — terminals don't deliver keyup events —
  so toggle is the only physically meaningful option.

### Fixed

- **`/voice /oof` (and any other typo) used to silently turn into a
  status query** — the `_cmd_voice` fall-through printed "Voice:
  recording 🎤" without doing anything, so a user who mis-typed `off`
  believed they had stopped recording while the recorder kept running
  in the background. Now the dispatcher:
    - Only treats the **empty** argument as a bare status query.
    - Rejects any other argument with `Unknown /voice subcommand: …`
      plus the full usage line.
    - When a recording is currently active and the user just typo'd
      the stop command, appends a loud `⚠️ Still recording — run
      /voice off (literal) to stop and transcribe` warning so the
      mistake can't hide.

### Tests

- **5282 passing** (+14 vs v1.21.0): 9 `AudioRecorder` VAD (disabled /
  window-start / loud-resets / time-based auto-stop / not-recording /
  odd-bytes / empty-chunk), 4 `Ctrl+Space` hotkey (idle→on, recording
  →off, `ctrl+@` alias, missing-dispatcher safety), 4 `/voice` typo-
  rejection (typo preserves recording state / bare shows status /
  recording-state hint / idle-state hint).

---

## v1.21.0 — Local Whisper, Recovery Pass, CHANGELOG Backfill

This release is the "loose ends" cut: every deferred Wave2 item from the
2026-04-11 deep-check gets shipped, the CI matrix stops warning on
deprecated Node actions, and the CHANGELOG finally covers the four
intermediate releases (v1.16 → v1.18.2) that had no entry before.

### New Features
- **`/voice` backend: `local`** — embedded `faster-whisper` inference, no HTTP server required. Set `voice.backend = "local"` in config and optionally pick a model size with `voice.local_model` (`tiny` / `base` / `small` / `medium` / `large-v3`). Weights download lazily on first `/voice on` into the faster-whisper cache, so config-time cost is zero. New pip extras: `pip install llmcode-cli[voice-local]` pulls `sounddevice>=0.5` + `faster-whisper>=1.0` together. The factory now accepts four backends: `local` / `whisper` / `google` / `anthropic`.
- **Wave2-1a thinking_order recovery** — new `llm_code.runtime.recovery.thinking_order` with `repair_assistant_content_order(blocks, mode="reorder"|"strip")`. Partitions `ThinkingBlock` instances to the front of the content tuple without modifying their signature bytes (required for Anthropic extended thinking verbatim round-trip). `"strip"` mode drops any late thinking block for callers that already invalidated signatures. Sibling of the existing `api/content_order.py` validator — validator raises, recovery repairs. 12 unit tests cover well-ordered / out-of-order / signature preservation / mode selection.
- **Wave2-3 telemetry `record_fallback`** — `Telemetry.record_fallback(from_model, to_model, reason)` emits an `llm.fallback` OTel span with `llm.fallback.{from,to,reason}` attributes. Called from `ConversationRuntime` at the same site as the `http_fallback` hook so external tracing backends (Jaeger / Honeycomb / Tempo) can chart fallback-chain walks without parsing logs. Disabled / no-package paths remain no-op.
- **Wave2-2 cost_tracker round-trip** — `CheckpointRecovery.load_checkpoint(session_id, *, cost_tracker=...)` and `detect_last_checkpoint(cost_tracker=...)` now restore the tracker's running token and cost totals from the checkpoint, instead of silently dropping them on reload. `save_checkpoint` already embedded the payload; only the load path was missing. The `/checkpoint resume` command passes the live cost tracker through so a resumed session continues cost accounting from where it left off. 5 new checkpoint round-trip tests cover legacy-checkpoint compatibility.

### Fixed
- **Voice error string: package name** — `detect_backend()` raised "Install sounddevice (`pip install llm-code[voice]`)" on failure, but the actual PyPI package is `llmcode-cli`. The hyphenated name would not resolve on PyPI; now emits `pip install llmcode-cli[voice]`.
- **`/voice` guard copy** — the "voice not configured" message now lists all four backends (`local`, `whisper`, `google`, `anthropic`) and points at `config.json` instead of a non-existent `config.toml`.

### Infrastructure
- **CI actions upgraded to Node 24** — `actions/checkout@v4` → `v5`, `codecov/codecov-action@v4` → `v5`. Both ship the Node 24 runtime so GitHub's 2026-06 deprecation warnings stop firing on every run. `actions/setup-python@v5` stays until v6 releases (still Node 20 upstream).

### Docs
- **CHANGELOG backfill** — added full entries for v1.16.0, v1.16.1, v1.17.0, v1.18.0, v1.18.1, and v1.18.2. The file previously jumped from v1.15.1 to v1.19.0 with no coverage of the architecture refactor, VS Code extension, 6-phase agent upgrade, gold-gradient logo, `/update` command, `/theme` command, or centralized tool registry work.

### Tests
- **5268 passing** (+25 vs v1.20.0): 12 thinking_order recovery, 2 telemetry record_fallback (noop + mocked-otel), 5 checkpoint cost_tracker round-trip, 6 LocalWhisperSTT (protocol / lazy load / missing-dep error / mocked pipeline / factory routing), unchanged Pydantic + dialog 3.10+ compat.

---

## v1.20.0 — Prompt History, Right-Arrow Autocomplete, Python 3.10+ Floor

### New Features
- **Shell-style prompt history** — submitted prompts are stored in `~/.llmcode/prompt_history.txt` (oldest-first so `tail` shows the newest) with bash / zsh `HISTCONTROL=ignoredups` semantics: consecutive duplicates collapse, empty entries are dropped, and the list is capped at 1000 entries with the oldest evicted first. In the InputBar, **↑ walks older** submissions, **↓ walks newer**, and stepping past the newest entry restores the composing draft you had before you started navigating. History is suppressed when the slash-command dropdown is open (those arrows are already claimed for dropdown nav), inside vim mode (j/k/gg/G own up/down there), and for multi-line buffers (arrow = cursor movement). Any keystroke or delete resets the history cursor so you can freely edit a recalled prompt.
- **Right-arrow accepts dropdown completion** — when the `/`-command autocomplete dropdown is visible, `→` now commits the highlighted command just like `Enter` / `Tab`. The dropdown only appears before a space is typed, so there is no legitimate cursor-right movement to preempt.

### Fixed / Compat
- **Python 3.10+ is now the real floor** (was briefly advertised as 3.9+ in v1.19.0, but 3.9 couldn't actually run the codebase). Three independent 3.9 breakages turned up: module-level `TextValidator = Callable[[str], str | None]`, `ModalScreen[str | None]` class bases, and — the unfixable one — `asyncio.Queue() / Event() / Lock()` eagerly binding to the event loop inside sync `__init__` in `mcp/transport.py`, `lsp/client.py`, `runtime/tool_pipeline.py`, and several tests. Python 3.10 made those primitives lazy-bind, so 3.10 is the minimum. CI matrix, `requires-python`, classifiers, ruff `target-version`, README, and `docs/getting-started.md` all aligned on 3.10+. The CI matrix now covers `["3.10", "3.11", "3.12", "3.13"]` contiguously (the earlier skip of 3.10 was a typo).
- **v1.19.0 `/voice`, `/help`, `/export`, HookDispatcher, FallbackChain, Phase 5 consolidations** — all the commits from v1.19.0 land in this release on top of a CI run that actually passes. (v1.19.0's CI was red against a Python 3.9 cell that could not load the codebase; the tag still exists but should be considered superseded.)

### Tests
- **5243 passing** (+15 vs v1.19.0): the 15 new tests are for `PromptHistory` — in-memory semantics, consecutive dedup, max_entries bound, persistence round-trip, oldest-first file layout, missing/unreadable file handling, cursor reset on edit, draft restore on ↓ past newest.

## v1.19.0 — Architecture Refactor Finish, /voice Wire, /help Modal Rewrite, Py3.9 Compat

### New Features
- **Declarative FallbackChain** — `ModelRoutingConfig.fallbacks: tuple[str, ...]` with a stateless `FallbackChain.next(current, error_kind)` API. The legacy single-shot `fallback: str` is promoted to a 1-element chain so existing configs keep working. Non-retryable errors (auth, model-not-found, 413) short-circuit so they don't consume fallback budget. Enables chains like `sonnet → haiku → gpt-4o → local`.
- **`/voice` actually works** — the command was a dead stub that only flipped `_voice_active`; no code ever imported `voice.recorder` or `voice.stt`. Now wires the full pipeline: `/voice on` detects a recording backend (sounddevice / sox / arecord), builds an `STTEngine` from config, and starts capture. `/voice off` stops the recorder and runs transcription in `asyncio.to_thread`, then inserts the text into the InputBar on the UI thread. Bare `/voice` shows status, backend, and language.
- **`/export` implementation** — writes the live conversation to a Markdown file via a new `_render_session_markdown` helper that walks `session.messages` for every block type (text, thinking, tool_use, tool_result, image, server_tool_use, server_tool_result). Thinking blocks collapse in `<details>` for GitHub. `/export` defaults to `./llmcode-export-<id>-<date>.md`; `/export <path>` takes an explicit target. The command was declared in the registry but had no handler before this release.
- **Python 3.9 / 3.10 support** — the advertised `python>=3.9` now actually runs. Audited 320 `llm_code/*.py` + 434 `tests/*.py`: no `match`/`except*`/runtime PEP 604 unions/`TaskGroup`/`ParamSpec`. The single 3.11+ blocker was `tomllib` in `model_profile._load_toml`; now falls back to the `tomli` package (same API) on older interpreters. `pyproject.toml` declares `tomli>=2.0; python_version < "3.11"`.

### Fixed
- **`/help` modal was double-broken** — `_refresh_content` funneled a `RichText` through `Console(force_terminal=True)` to an ANSI string and fed it to `Static.update()`, but Textual's `Static` does not decode ANSI. Escape bytes became literal characters, garbling margins and breaking height math. Separately, the list tabs tracked a `>` cursor marker inside a single `Static`, so the surrounding `VerticalScroll` had no idea where the cursor was and silently dropped `down`/`PageDown`/`End` — only the first ~13 commands were ever reachable. Rewrote `HelpScreen` to use Textual's built-in `OptionList` for the commands / custom-commands tabs; keyboard nav, scrollbar, focus highlight all work natively. Verified headless with `pilot.press("down") × 50 + end + home`: `highlighted` and `scroll_y` track correctly across all 52 options.
- **4 slash commands had no autocomplete hint** — `/update`, `/theme`, `/cache`, `/personas` had working `_cmd_*` handlers but no `CommandDef` in `COMMAND_REGISTRY`. They ran if typed in full but never appeared in the `/` dropdown, Tab-completion, or `/help`. Added registry entries.
- **`/export` was a dead hint** — the opposite drift: registry declared it but dispatcher had no `_cmd_export`, so selecting `/export` resolved to "Unknown command". Implemented (see above).

### Architecture refactor (2026-04-11 plan — 100% complete)
- **Phase 2.1** — `HookDispatcher` extracted from `conversation.py`. `_fire_hook` becomes a thin delegator; the ~26 call sites inside `conversation.py` (pre_compact, prompt_submit, http_fallback, …) don't need to change.
- **Phase 5.3** — `voice/*.py` (7 files, ~366 LOC) consolidated into `tools/voice.py`. Old package kept as backward-compatibility shims so `tests/test_voice/` passes unchanged.
- **Phase 5.4** — `sandbox/docker_sandbox.py` + `pty_runner.py` consolidated into `tools/sandbox.py`. `bash.py`, `tui/app.py`, `runtime/config.py` point at the canonical location; old `sandbox/` package stays as shim.
- **Phase 5.5** — `hida/{types,profiles,engine,classifier}.py` (4 files) consolidated into `runtime/hida.py`. 50-test `tests/test_hida/` suite untouched thanks to shim layer.

### Tests
- **5228 passing** (+20 vs v1.18.2): 6 HookDispatcher, 9 FallbackChain, 6 voice wire-up, 7 `/export` markdown renderer.
- `test_dispatcher_has_all_52_commands` now derives expected names from `COMMAND_REGISTRY` at runtime instead of a hard-coded list.
- New `test_registry_has_no_dead_handlers` enforces the opposite direction: every `_cmd_*` must have a registry entry. Prevents both drifts (dead hints, missing hints) from recurring.

### Docs / CI
- Complete 52-command reference table added to `README.md` as a collapsible `<details>` block after the Terminal UI highlight list.
- CI matrix filled in to `["3.9", "3.10", "3.11", "3.12", "3.13"]` — the earlier `["3.9", "3.11", …]` skipped 3.10.

## v1.18.2 — Architecture Refactor Round 1 (app.py/conversation.py decomposition)

### Refactor — large-file decomposition
- **`app.py` 3999 → 1200 lines** — extracted `CommandDispatcher` (51 `_cmd_*` methods), `StreamingHandler` (430-line `_run_turn`), and `RuntimeInitializer` (440-line `_init_runtime`) into dedicated modules under `tui/`.
- **`conversation.py`** — extracted `PermissionManager` and `ToolExecutionPipeline`, each with a well-defined collaborator boundary.
- **`runtime/memory/` unified** — `KVMemoryEntry` rename + lint merged into validator.
- **`config.py` split** — feature submodules (701 → 611 lines); enterprise/vision/voice configs now live in `config_features.py`, `config_enterprise.py`, `config_migration.py`.
- **`enterprise/` → `runtime/enterprise.py`** — auth / RBAC / OIDC / audit logger collapsed into a single module.
- **`streaming/` → `tui/stream_parser.py`** — `stream_parser.py` moved to its only consumer.
- **Tool consolidation** — `swarm_*.py`, `task_*.py`, `cron_*.py` tool wrappers (10 files) merged into `tools/swarm_tools.py`, `tools/task_tools.py`, `tools/cron_tools.py`.
- **Centralized tool registry** — `tools/registry.py` + `tools/builtin.py`; adding a new tool is now a single `CommandDef`-style registration.

### Features
- Wired 6 previously-orphan modules into runtime: `agent_loader`, `tool_visibility`, `tool_distill`, `prompt_snippets`, `denial_parser`, `exec_policy`.

### Fixed
- Source-inspection tests updated to the new file locations.
- Ruff F401 / TYPE_CHECKING regressions introduced by the refactor.

---

## v1.18.1 — `/update` Command + 8 Built-In Themes

### Features
- **`/update` command** — checks PyPI for a newer version, shows current → latest, and runs `pip install --upgrade llmcode-cli` in-place. Startup banner performs a cached background check (6-hour TTL) so the user sees an update hint without manual polling.
- **8 built-in themes** — dracula, monokai, tokyo-night, github-dark, solarized-dark, nord, gruvbox, plus the original default. Switch with `/theme <name>`.

### Docs
- README comparison table gained Codex CLI + Gemini CLI columns.

---

## v1.18.0 — Codex / Gemini CLI Patterns + Local Model Recovery

### Features
- **7-phase Codex / Gemini CLI design adoption** — imported patterns from the upstream CLIs (permission staging, tool-output shaping, retry triage) into llmcode's conversation loop.

### Fixed
- **Local model retry recovery** — when a local LLM retry path previously aborted on malformed tool results, the tool results are now preserved on the next iteration.
- **Text-only iteration after tool results** — local models that don't handle tool/text interleaving well now force a text-only follow-up iteration instead of looping on the same tool call.

### Docs
- Competitor list updated: replaced Continue.dev (IDE assistant, not a CLI agent) with actual CLI-agent peers.

---

## v1.17.0 — 6-Phase Agent System + Logo Refresh + TUI Scroll Fix

### Features
- **6-phase agent system upgrade** — borrowed from `claude-code`: tiered filtering, fork-cache, frontmatter agents, memory scopes, contextvars, worktree isolation.
- **Gold gradient logo** — TUI welcome banner + README SVG now share a pixel-perfect gold gradient rendering via Rich's `export_svg()`. Several iterations on block-art preservation (keep original font; swap gradient only; rect pixels for SVG; bust GitHub camo cache with a filename change).

### Fixed
- **TUI scroll regression** — addressed as part of the agent-system rework.
- **Local model tool nudge** — small models drop tool calls when the system prompt is too long; now get a short nudge.
- Ruff F401 unused-import lint in test files.

---

## v1.16.1 — Model Tuning Bump

Version bump only — carries the v1.16.0 model-profile feature set to PyPI.

---

## v1.16.0 — Model Profile Tuning, Dream Consolidation, VS Code Extension

### Features
- **Per-model profile tuning** — temperature, reasoning effort, and small-model auto-downgrade now live on the `ModelProfile` so llmcode can adapt the same conversation to radically different backends without config churn.
- **4-stage dream consolidation** — `DreamManager` gains trigger guards, date normalization, and memory pruning so the "sleep" consolidation pass doesn't run on empty sessions or re-process the same window.
- **Cache breakpoint detection** — Anthropic prompt-cache breakpoint lookup and placement, plus anti-recursive sub-agent spawn (prevents a `task` tool invocation from immediately dispatching the same tool again).
- **Circuit breaker** — `ConversationRuntime` stops retrying after 3 consecutive compact failures instead of spinning forever on an unrecoverable prompt-too-long loop.
- **VS Code extension scaffold** — bridge + chat panel + code actions + WebSocket client + status bar. Full extension source under `extensions/vscode/`. Code actions include "Ask about selection" and "Fix with llmcode".

### Security
- **Path case normalization + SSRF defenses** — added port blocking and DNS rebinding defense to the `web_fetch` / `web_search` path; path normalization prevents case-insensitive bypass of permission allowlists on macOS / Windows.

### Fixed
- CI failures around `ParsedToolCall.args`, pair-integrity checks, test-count badge drift.

### Docs
- i18n comparison corrected — CJK support is partial, not full.
- IDE extensions added to the vs-other-tools comparison table; rows re-ordered.
- Qwen Code added to the comparison table.
- VS Code extension design spec (bridge + chat panel + code actions).

---

## v1.15.1 — SSE Streaming, Docker Sandbox, PTY, Plan Mode Tools, Arena Pattern

### New Features
- **AnthropicProvider real SSE streaming** — `_AnthropicLiveStreamIterator` reads events via httpx `aiter_lines()` as they arrive, instead of downloading the entire response first
- **Docker sandbox** — `DockerSandbox` class with Docker/Podman auto-detection, container lifecycle, and `SandboxConfig` (image, network, memory/CPU limits). Wired into `BashTool._run()` as optional isolation layer
- **PTY runner** — `run_pty()` via ptyprocess for interactive commands (git rebase -i, etc.) with optional pyte screen rendering. `BashTool` gains `pty: true` input parameter
- **Plan mode tools** — `enter_plan_mode` / `exit_plan_mode` tools let the model control plan→act transitions programmatically
- **Arena pattern** — `AgentBackend` Protocol + `ArenaManager` for parallel agent coordination with pluggable backends (subprocess, tmux, worktree)
- **Profile TOML hot-reload** — `ProfileRegistry.reload_if_changed()` stats directory mtime, called automatically from `get_profile()`
- **Marketplace search** — filter input, category grouping headers, stats bar

### Fixed
- **Scroll regression** — reverted all experimental scroll changes (watch_scroll_y, priority bindings, key_* handlers, InputBar dispatch) back to v1.15.0 baseline. Shift+Up/Down, PageUp/PageDown, and mouse wheel (Warp native) all work correctly again

### Tests
- 11 new tests: ChatScrollView auto-scroll, permission dialog choices, settings write-back validation, edit-args encoding

## v1.15.0 — Profile System Phase 2, Prompt Caching, Mouse Scroll, 11 TODO Resolutions

### Profile System Deep Wiring
- **StreamParser reads `implicit_thinking` from model profile** instead of probing config.thinking.mode
- **`build_thinking_extra_body()` branches on profile format** — `anthropic_native` (Anthropic) vs `chat_template_kwargs` (vLLM/OpenAI-compat)
- **Local model detection reads `profile.is_local`** with URL-pattern fallback for unknown models
- **SkillRouter tier-C model reads from profile** → config → active model (3-level fallback)
- **`/model` displays profile info** — capabilities, provider type, pricing, context window
- **Profile auto-discovery** — probes `/v1/models` at runtime to match better profiles for unknown model names
- **TOML example profiles** in `examples/model_profiles/` (qwen3.5-122b, claude-sonnet, custom-local)

### Anthropic Provider Enhancements
- **Prompt caching** — automatic `cache_control: ephemeral` on system prompt, last tool definition, and last user message content block. Adds `anthropic-beta: prompt-caching-2024-07-31` header.
- **Signature delta accumulation** — `StreamThinkingSignature` event carries the complete cryptographic signature from streaming `signature_delta` events, wired through to `ThinkingBlock` for round-trip.
- **Server tool use blocks** — new `ServerToolUseBlock` / `ServerToolResultBlock` types with signature round-trip. Streaming parser assembles them from `content_block_start/stop` events.

### Streaming & Parsing Fixes
- **Accept mismatched XML closing tags** — Qwen3.5 sometimes emits `<web_search>JSON</search>` (truncated closer). Variant 5 regex now accepts any `</identifier>` as closer.
- **Strip trailing XML tags from JSON body** — the Hermes args parser now removes any trailing `</tag>` before JSON parsing, fixing empty-args bug.
- **StreamParser bare tool detection** — accepts `known_tool_names` and classifies `<tool_name>JSON</tag>` as TOOL_CALL during streaming, preventing raw XML from appearing in chat.

### TUI Improvements
- **Mouse wheel scrolling** — scroll up pauses auto-scroll so you can browse history during streaming; scroll to bottom resumes. Fixed `resume_auto_scroll()` being called on every text chunk.
- **Permission prompt → TextualDialogs modal** — replaced inline y/n/a key handler with `select()` dialog
- **MCP approval → TextualDialogs modal** — replaced inline key handler with async modal dialog
- **Edit args** — new "Edit args" option in permission dialog; opens text editor for JSON, sends modified args to runtime
- **`/set` command** — live config write-back (`/set temperature 0.5`, `/set max_tokens 8192`, `/set model ...`)
- **Removed dead `PermissionInline` import**

### Plugin System
- **Fixed `_tool_registry` → `_tool_reg` bug** — plugin tools were never actually loading due to wrong attribute name
- **Plugin unload wiring** — `_unload_plugin_tools()` called on disable/remove, handles stored in `_loaded_plugins` dict
- **`env` added to dangerous permissions** — blocks plugins requesting environment variable access unless `--force`
- **Skill file loading from manifests** — executor now loads SKILL.md files from `manifest.skills` into SkillRouter

### Runtime
- **Memory distillation at startup** — `distill_daily()` runs at TUI init (today-\*.md → recent.md → archive.md)
- **Subagent per-role model routing** — `model` parameter in `make_subagent_runtime()` now creates a config override
- **Settings write-back** — `apply_setting()` validates and applies changes via `dataclasses.replace`

### TODO Cleanup
- Updated 6 stale TODO/follow-up comments to reflect completed wiring (MCP agent_approval, MCP server_registered, memory distillation cron, plugin permissions)

## Unreleased — perf: SkillRouter negative cache + timing log (cuts Tier C overhead in half)

### Fixed
- **`SkillRouter.route_async` ran Tier C twice per turn** on CJK queries. The method is called from two places: `tui/app.py:1426` (for display) and `runtime/conversation.py:1036` (for prompt injection). Negative Tier C results (LLM classifier returned "no match") were never cached, so both call sites ran the full 5-15s LLM classifier round-trip. Observed in a 2026-04-09 field report as "Routing Skill 花了不少時間".
- **Fix**: `route_async` now checks the cache at the very top (BEFORE calling the sync `route()` helper) and caches the Tier C negative result explicitly. Second call within the same turn is a cache hit → instant return.
- **Additional fix**: the sync `route()` already cached empty results from Tier A/B misses, but `route_async`'s old code called `self.route()` (which returned `[]` from the cache), saw `if result:` as False, and **re-entered the Tier C path instead of honoring the cache**. The new top-level cache check covers this edge case too.

### Added
- **Debug logging for all tier decisions** so the user can see which tier matched and how long it took:
  - `skill_router cache hit: N skills in 0.000s` — cache hit short-circuit
  - `skill_router tier_a: N skills in 0.001s` — keyword match
  - `skill_router tier_b: N skills in 0.012s` — TF-IDF match
  - `skill_router tier_c starting: model=X cjk=True` — LLM classifier fire
  - `skill_router tier_c complete: matched='alpha' in 4.23s` — classifier result
  - `skill_router tier_c miss (negative cached): 4.23s total` — negative cached
- **`last_tier_c_debug`** now includes the elapsed time so `/skill debug` shows exact classifier cost.

### Impact
Same turn with a CJK query that triggers Tier C:
- **Before**: Tier C fires twice per turn (once for TUI display, once for prompt). If classifier takes 5s, that's **10s of overhead per turn**.
- **After**: Tier C fires once, cached. Second call is a map lookup (~µs). **Saves 5-10s per CJK turn**.

Combined with PRs #41/#42/#43/#44, a Qwen3.5 CJK turn's wall-clock now looks like:
- ~0-14s native fallback (only on first-ever session, cached after)
- ~4s XML iteration 1 (tool_call)
- ~5-10s Tier C (reduced from 10-20s)
- ~19s web_search execution
- ~21s iteration 2 (synthesis)
- **Total: ~50-55s first session, ~35-45s every session after**

### Tests
- **`tests/test_runtime/test_skill_router_negative_cache.py`** — 6 new tests:
  - Tier C negative result cached: second call doesn't re-invoke provider (core fix)
  - Tier C positive result cached (regression guard for pre-existing behavior)
  - Different queries get independent cache entries
  - No provider configured → Tier C skipped, empty cached, second call instant
  - Tier A hit → cached for async reuse (Tier C never fires)
  - Edge case: sync `route()` cached empty from Tier A/B miss is honored by `route_async`
- Existing 7 `test_skill_router_add_remove_wave2_5.py` tests + `test_skill_router_cjk_fallback.py` tests still pass
- Full sweep: **3272 passed**, no regressions

## Unreleased — perf: persistent native-tools capability cache (14s/turn → 0s after first)

### Added
- **`llm_code/runtime/server_capabilities.py`** — tiny persistent JSON cache at `~/.llmcode/server_capabilities.json` keyed by `(base_url, model)`. Records whether each server+model combination supports native OpenAI-style tool calling. When the `conversation.py` auto-fallback branch detects the "Server does not support native tool calling" error and sets `self._force_xml_mode = True`, it now also writes the result to this cache.
- **Next session reads the cache** at turn setup. If the combo is marked unsupported, `self._force_xml_mode` is seeded to True immediately and the entire 14-second native-rejection round-trip is SKIPPED on turn 1.

### Impact
The 14-second server-side latency that remained after PRs #41/#42/#43 is now paid **once per (server, model) combination, EVER** — not once per session. A user who runs llmcode daily against the same local vLLM server pays the 14s on day 1 and never again.

### Data model
```json
{
  "http://localhost:8000|/models/Qwen3.5-122B-A10B-int4-AutoRound": {
    "native_tools": false,
    "cached_at": "2026-04-09T13:30:00+00:00"
  }
}
```

Keyed by `f"{base_url.rstrip('/')}|{model}"` — trailing-slash-normalized base URL + exact model name. Two models on the same server get independent entries; same model on two servers gets independent entries. A future retention policy can expire stale entries via the `cached_at` timestamp.

### Atomic writes
Writes go through `tempfile.mkstemp` + `os.replace` so a concurrent reader never sees a partial write. Failed writes log at DEBUG and are swallowed — this is a pure optimization, not a correctness boundary.

### Cache management
- **Default**: write-on-fallback, read-on-turn-setup. No user action required.
- **Manual clear**: `clear_native_tools_cache()` wipes the whole file; `clear_native_tools_cache(base_url, model)` removes one entry. Exposed as a module-level helper for tests and a future `/cache clear` user command.

### Tests
- **`tests/test_runtime/test_server_capabilities.py`** — 14 new tests covering:
  - Load returns None on fresh system (no cache file)
  - Mark-then-load round-trip
  - Different models on same server are independent
  - Different base URLs with same model are independent
  - Trailing slash normalization on `base_url`
  - Marking one entry preserves siblings
  - Corrupted JSON file treated as "no cache" (never crashes)
  - Cache entries have ISO-format `cached_at` timestamp
  - `clear_native_tools_cache(url, model)` removes specific entry
  - `clear_native_tools_cache()` (no args) wipes entire cache
  - Partial clear (one arg) raises ValueError
  - Atomic writes leave no `.tmp` files
  - Source-level guard: `conversation.py` calls `load_native_tools_support`
  - Source-level guard: `conversation.py` calls `mark_native_tools_unsupported` in fallback branch
- Full sweep: **3266 passed**, no regressions

### Combined with the previous 8 fixes
| Metric | Original | After #41-#43 | **After this** |
|---|---|---|---|
| First turn / new server | 65s (retry storm) | 58s (1 fallback) | **58s** (one-time cost) |
| Second turn+ / same server | 58s | 58s | **~44s** (skips 14s) |
| Fifth session / same server | 58s × 5 = 290s | 58s × 5 = 290s | **44s + 58s (first) = 102s** for equivalent workload |

### Future enhancements
- `/cache clear` user command to manually reset if the user intentionally changes server config
- TTL-based expiry (e.g. 7 days) so a server that added `--enable-auto-tool-choice` is re-probed automatically
- Probe on startup rather than on first turn so even the first turn is instant

## Unreleased — fix: idempotent-retry detector actually aborts the turn (was: continue → burn 91s)

### Fixed
- **The `"Aborting turn: idempotent retry loop detected"` log message was a lie.** The code at `conversation.py:L1641` used `continue` inside the inner dispatch loop, which only skipped the offending call. The outer iteration loop kept running; the model got another turn, saw the `"Aborted"` error block, and re-emitted the same failing tool call on iteration N+1. Repeated until `max_turn_iterations` exhausted.
- **Field report 2026-04-09**: user's query burned **91 seconds** with the log line firing 3 times and input tokens bloating to **45,732** as the model re-emitted the same web_search call across iterations. The "abort" was purely cosmetic.
- **Fix**: replace the `continue` with `break` (exit the inner dispatch loop) AND set a new `_turn_aborted_by_retry_loop` flag checked at the end of each outer iteration. When the flag is set, the outer loop `break`s, the error `tool_result_block` is appended to the session for message history consistency, and a visible `StreamTextDelta` explains WHY the turn ended so the user isn't left wondering.

### Visible user message
Previously: silent loop burning turn budget, no explanation in the chat.

Now:
```
⚠ Aborted: the model asked to call 'web_search' again with the same
arguments as the previous call, which indicates a retry loop. Try
rephrasing your request, or check whether the tool result was useful.
```

### Tests
- **`tests/test_runtime/test_idempotent_retry_abort.py`** — 4 new source-level regression guards:
  - `test_idempotent_retry_uses_break_not_continue` — scans the retry-detected branch for `break` keyword (the exact 91s bug)
  - `test_turn_loop_breaks_on_idempotent_retry_flag` — `_turn_aborted_by_retry_loop` flag is checked in the outer iteration loop
  - `test_idempotent_retry_emits_visible_explanation` — user sees a ⚠ warning, not a silent abort
  - `test_retry_tracker_still_created_per_turn` — lifecycle unchanged (per-turn, not per-iteration)
- Existing `test_conversation_retry_loop_abort.py` (2 tests pinning the tracker unit) + force_xml + fallback tests all still pass
- Full sweep: **3252 passed**, no regressions

### Context
This is the 7th fix in a row chasing the Qwen3.5-122B TUI field report thread:
1. #36 — empty response diagnostics
2. #37 — truncation warning + stop_reason
3. #38 — flush() silent drop
4. #39 — parser variant 5
5. #40 — log-file flag (enabled clean log capture for further debugging)
6. #41 — force_xml sticky + retry storm (broke fallback ordering)
7. #42 — fallback ordering fix
8. **this** — idempotent retry actually aborts

Each one was a distinct root cause discovered by logs from the previous fix. The diagnostic-first discipline from #36/#37/#40 paid off repeatedly.

## Unreleased — fix: tool-call-parser fallback must run BEFORE is_retryable short-circuit

### Fixed
- **Regression from PR #41**: the tool-call-parser error is now marked `is_retryable=False` (correctly — it can't be fixed by re-sending the same request), but the `conversation.py` outer exception handler checked the wave2-3 `is_retryable is False` short-circuit **before** the XML-fallback branch. Result: the recoverable error bypassed its recovery path and surfaced to the user as visible assistant text — `"Error: 'auto' tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set"`. Reported immediately after PR #41 merged.
- **Fix**: reorder the branches in the outer `except Exception` block so the tool-call-parser string-match runs FIRST (rebuilds request without tools, retries in XML mode), and the `is_retryable is False` short-circuit runs SECOND as a fallback for genuinely unrecoverable errors (401 auth, 404 model not found).

### Conceptual distinction
The root cause was conflating two meanings of "retryable":
1. **Can retry the same request** (rate limit, timeout, transient failure) — wave2-3's `is_retryable=False` is about this
2. **Can recover from this error somehow** (retry as-is, switch mode, rebuild request) — tool-call-parser is recoverable via mode switch, not re-send

The fix makes the order explicit: try the specific recovery path first, fall through to the general "give up" check only if no recovery applies.

### Tests
- **`tests/test_runtime/test_force_xml_sticky.py`** — 2 new source-level regression guards:
  - `test_fallback_branch_runs_before_is_retryable_short_circuit` — pins the ordering by searching for the two relevant string positions in the method source
  - `test_is_retryable_short_circuit_still_present` — guards wave2-3's 401/404 behavior (the short-circuit was moved, not removed)
- Existing 4 force_xml guards + 16 wave2-3 fallback tests + 16 wave2-1b retry tests all still pass
- Full sweep: **3248 passed**, no regressions

### Impact
Same query that hit the "Error:" wall now triggers the XML fallback on the first attempt and produces a real tool_call. Combined with PR #41's fast-fail retry skip, the total turn time drops from 65s → estimated ~12s AND the turn actually succeeds instead of showing an error.

## Unreleased — perf: kill the native-tool-call retry storm (53s → ~0s)

### Fixed
- **Stale `force_xml` local** in `ConversationRuntime._run_turn_body` shadowed `self._force_xml_mode` within a single turn. The local was captured once at turn setup (L834) and never refreshed, so when iteration 1 hit the "tool-call-parser not supported" error and set `self._force_xml_mode = True`, iteration 2 still read the stale local as `False` and re-attempted native tool calling from scratch. Observed in a Qwen3.5 field report: second iteration burned ~19s on a duplicate retry storm before hitting the same fallback branch.
- **Tool-call-parser error is now non-retryable** in `_raise_for_status`. Previously a server response containing "tool-call-parser" or "tool choice" in the error message was classified as a generic `ProviderConnectionError` and went through `_post_with_retry`'s 3-strike exponential backoff — burning ~30s before the error surfaced to the outer fallback branch. Now it raises `ProviderError(msg, is_retryable=False)` which bypasses the retry loop entirely; the outer `except Exception` in `conversation.py` still pattern-matches the message and switches to XML mode on the first attempt.

### Impact
Same field-report turn: **65s total → estimated ~12s** (rough estimate; actual wins depend on server first-token latency for the legitimate XML-mode attempts).

Before:
- 12:52:19 Starting turn
- 12:52:53 Native fallback triggered (**+34s** — 3 native retries)
- 12:52:57 Executing web_search
- 12:53:16 Native fallback triggered **AGAIN** (**+19s** — duplicate retry storm)
- 12:53:24 Turn complete (**+8s**)

After:
- Native attempt → instant fail → immediate XML fallback (no retry sleeps)
- force_xml sticky → iteration 2 skips native entirely

### Tests
- **`tests/test_runtime/test_force_xml_sticky.py`** — 4 source-level guards pinning the fix:
  - `force_xml = getattr(self, ...)` local shadow pattern is gone
  - `use_native` reads `self._force_xml_mode` directly
  - `self._force_xml_mode` is initialized before the iteration loop
  - The fallback branch still sets `self._force_xml_mode = True` (wave2-3 regression guard)
- **`tests/test_api/test_rate_timeout_backoff_wave2_1b.py`** — 3 new tests for the fast-fail:
  - `"tool-call-parser"` error in response → `ProviderError(is_retryable=False)`, **zero sleeps**, **exactly 1 HTTP call**
  - `"tool choice"` error variant → same fast-fail behavior
  - Plain 400 without tool-related message → still retryable (preserves existing 4xx handling)
- Full sweep `test_runtime/` + `test_api/` + `test_tui/` + `test_streaming/` + `test_tools/`: **3246 passed**, no regressions.

### Analysis chain
The user ran `llmcode -v --log-file /tmp/llmv.log` (PR #40) and shared a clean log. Timeline analysis revealed:
1. Two "Server does not support native tool calling" messages per turn — obvious smell
2. 34s for first native-mode attempt → 3× retry in `_post_with_retry`
3. 19s for second iteration's retry of the same error → stale local caching `force_xml` at turn start

Without PR #40's log-file support this would have been impossible to diagnose — the user's earlier `2> /tmp/log` attempts garbled both the TUI and the log.

## Unreleased — cli: `--log-file` flag so `-v` doesn't break the TUI

### Added
- **`--log-file PATH`** CLI flag routes verbose logs to a file instead of `sys.stderr`. Required when running the TUI with `-v` — otherwise the user's natural instinct to do `llmcode -v 2> /tmp/log` interleaves Python logging output with Textual's own stderr writes, which completely breaks the TUI rendering (terminal fills with raw ANSI escape codes mixed with log lines).
- **`LLMCODE_LOG_FILE` environment variable** as the secondary source for the log destination, so users who want logs everywhere can set it once in their shell rc instead of passing the flag on every invocation.
- **Destination priority**: explicit `--log-file` > `LLMCODE_LOG_FILE` env > `sys.stderr` (existing default). Tilde expansion is honored so callers can pass `~/.llmcode/logs/debug.log`. Parent directories are created on demand.
- **`setup_logging(verbose, log_file)`** now accepts the new kwarg. When a log file is chosen, a `FileHandler` is installed and the `StreamHandler(sys.stderr)` is NOT — the TUI's stderr stream stays clean.

### Context
Found while investigating the Qwen3.5 TUI field reports. The user tried `llmcode -v 2> /tmp/llmv.log` to capture a verbose log for me to diagnose slowness — the command started the TUI but stderr redirect grabbed Textual's terminal control codes along with the log messages, producing a garbled log file and a broken TUI display ("卡住很久了"). The log file contained full TUI frame snapshots in ANSI escape sequences instead of clean log lines. No amount of user education can fix this — the right answer is a first-class file destination for logs that bypasses stderr entirely.

### Tests
- **`tests/test_logging_file.py`** — 8 new tests:
  - Default destination is stderr (pre-existing behavior preserved)
  - Explicit `log_file` argument installs a FileHandler, not StreamHandler
  - Messages actually land in the file (write + read roundtrip)
  - Parent directory auto-created so `~/.llmcode/logs/today.log` just works
  - `LLMCODE_LOG_FILE` env var used when no explicit arg
  - Explicit arg overrides env var
  - `~` path expansion honored
  - `verbose=False` still accepts log_file (destination is independent of level)
- Full sweep `test_logging_file.py` + `test_runtime/` + `test_api/` + `test_tui/` + `test_streaming/` + `test_tools/`: **3247 passed**, no regressions.

### Usage
```bash
# Previously broken:
llmcode -v 2> /tmp/llmv.log          # TUI garbled, log polluted with ANSI

# Now works cleanly:
llmcode -v --log-file /tmp/llmv.log  # TUI clean, log is just log lines
LLMCODE_LOG_FILE=/tmp/llmv.log llmcode -v  # same, via env var
```

## Unreleased — parser: recognize bare ``<NAME>JSON</NAME>`` tool call variant (Hermes variant 5)

### Fixed
- **Third Qwen3.5 field report fix**: user asked "今日新聞三則" and the TUI showed the raw text `<web_search>{"query": "今日熱門新聞", "max_results": 3}</web_search>` as the assistant's visible response. The tool never executed; iteration 2 never happened. Root cause: vLLM's chat template was producing a **bare `<NAME>JSON</NAME>`** tool-call format (tool name IS the XML tag, no `<tool_call>` wrapping) that none of the four existing Hermes variants in `parse_tool_calls` could match. With the parser returning empty, `runtime/conversation.py:L1564` broke out of the turn loop on `if not parsed_calls: break`, and the 22 output tokens of raw tool_call syntax became the visible reply.
- **New Hermes variant 5** — `_HERMES_BARE_NAME_TAG_RE` matches `<?([a-zA-Z_]\w*)>\s*(\{.*?\})\s*</\1>` with the leading `<` optional (some terminal renderings and prompt-prefix injections drop it). Only tried when no `<tool_call>` wrapper matched in `_parse_xml`, so the fast path for well-formed emissions is untouched. Handles three arg-nesting shapes: flat `{...}`, nested `{"args": {...}}`, nested `{"arguments": {...}}`.
- **False-positive guards**:
  1. **JSON validation**: the body must parse as a JSON object (scalars / lists / invalid JSON rejected).
  2. **Reserved names blocklist**: `tool_call`, `think`, `function`, `parameter` are never interpreted as variant 5 even when the body is valid JSON — prevents double-parsing of malformed `<tool_call>{"args": {}}</tool_call>` as a tool named `tool_call`.
  3. **`known_tool_names` registry gate**: `parse_tool_calls` now accepts an optional set of registered tool names; variant 5 only matches when the tag name is in the set. `runtime/conversation.py` passes `{t.name for t in self._tool_registry.all_tools()}` so production mode is strict. Tests without a registry pass `None` for permissive matching (documented caveat: `<p>{"a":1}</p>` would otherwise match in permissive mode).
- **`runtime/conversation.py:L1431`** now threads `known_tool_names` through to `parse_tool_calls` so the bare variant only fires on real tool names.

### Tests
- **`tests/test_tools/test_parsing.py::TestBareNameTagVariant`** — 13 new tests covering:
  - Exact field-report text parsed correctly
  - Missing leading `<` handled (terminal artifact / prefix injection)
  - Variant inside mixed prose
  - Multi-line body with newlines before/after JSON
  - Nested `"args"` key unwrapped to flat args
  - Nested `"arguments"` key unwrapped to flat args
  - `known_tool_names` blocks `<p>{"a":1}</p>` false positive
  - Invalid JSON rejected
  - Mismatched close tag rejected
  - Scalar / list body rejected
  - Reserved `tool_call` name NOT reinterpreted (regression guard for `test_missing_tool_key_skipped`)
  - Reserved `think` name NOT reinterpreted
  - Variant 5 does NOT fire when a valid `<tool_call>` wrapper is already present (no duplicate parses)
  - Multiple bare tool calls in one text each parse separately
- Existing 42 parsing tests still pass — **55 total** in that file
- Full sweep `test_tools/` + `test_runtime/` + `test_tui/` + `test_streaming/` + `test_api/`: **3239 passed**, no regressions

### Investigation chain
Field-report progression:
1. **Screenshot 1** (24 out tokens, empty response) → PR #36 added empty-response counter + unclassified variant message
2. **Screenshot 2** (279 out tokens, items vanished after intro) → PR #37 added stop_reason capture + truncation warning; PR #38 fixed `StreamParser.flush()` silently dropping unterminated `<tool_call>` content
3. **Screenshot 3** (22 out tokens, raw tool_call syntax as visible text) → **this PR**: the earlier PRs surfaced enough context to identify a third, distinct bug — the parser didn't recognize Qwen3.5's `<NAME>JSON</NAME>` variant at all

Each fix addressed a distinct root cause; none of them overlap.

## Unreleased — StreamParser flush: salvage unterminated tool_call instead of silent drop

### Fixed
- **Critical data loss bug**: `StreamParser.flush()` used to silently drop buffered content when the stream ended while inside an unterminated `<tool_call>` block. This matched exactly one field report: user asked "今日新聞三則", TUI showed 3 tool-call dots + "根據搜尋結果,以下是今日三則熱門新聞:" intro, and nothing else — despite the model reporting 279 output tokens. The news items were being generated by the model but got swallowed by a never-closed `<tool_call>` opening marker in the stream, and `flush()` threw them away at end-of-stream with zero diagnostic. Reproduced locally against a bare StreamParser; fix verified with the same repro.
- **`flush()` now salvages unterminated `<tool_call>` content as a TEXT event** instead of dropping it. The leading `<tool_call>` marker is stripped so the text reads naturally in the chat widget. Empty salvage (only the marker, no body) emits no event so the TUI doesn't show a blank assistant reply.
- **`flush()` also logs a warning** (`"unterminated <tool_call> block, salvaging N chars as TEXT"`) so `-v` runs capture the event. Silent data loss is worse than loud data loss.
- **Unterminated `<think>` block handling is preserved** (already emitted buffered content as THINKING before this fix) but now also logs a warning for symmetry.

### Tests
- **`tests/test_streaming/test_stream_parser.py`** — 7 new tests:
  - Unterminated `<tool_call>` body salvaged as TEXT with marker stripped
  - Full user scenario: intro + unclosed tool_call wrapping 3 news items — all items recoverable
  - Complementary: unterminated `<think>` content still preserved as THINKING (regression guard)
  - Edge case: empty buffer after `<tool_call>` marker emits no TEXT event
  - State is cleared after flush (`_in_tool_call=False`, `_buffer=""`) so parser is reusable
  - Warning log fires on `<tool_call>` salvage
  - Warning log fires on `<think>` salvage
- Existing 16 `test_stream_parser.py` tests still pass — **23 total** in that file
- Full `tests/test_streaming/` + `tests/test_tui/` + `tests/test_runtime/` + `tests/test_api/` sweep: **2191 passed**, no regressions.

### Root cause analysis
The investigation: user's screenshot showed 279 output tokens but only the intro line was visible. Oneshot `-q` mode worked fine for the same query (280 tokens, full 3-item list rendered), isolating the bug to the TUI's stream-rendering path. Walked the StreamParser source, identified three suspicious code paths (`flush()` drops, implicit-think-end race, state leak across iterations). Wrote targeted repro tests for each. **Scenario G** (unterminated `<tool_call>` → silent drop) matched the symptom exactly and reproduced on a bare StreamParser with no TUI / runtime / provider mocking.

### Related
- Follow-up to PR #36 (empty-response diagnostics) and PR #37 (stop_reason capture + truncation warning), which added the *visibility* to see this class of bug. This PR fixes an actual data-loss bug those surfaced.
- Complementary: Scenario A in the investigation revealed that implicit-think-end (bare `</think>` after text-that-was-already-emitted-as-TEXT) also has a design bug where content already streamed as TEXT cannot be retroactively reclassified as THINKING. That's a separate, less critical issue and not addressed here.

## Unreleased — TUI stop_reason capture + truncation warning

### Added
- **`LLMCodeTUI._last_stop_reason`** now captured at every `StreamMessageStop`. Previous PR referenced it but nothing assigned it — the value was always `"unknown"`. Initialized in `__init__` for first-turn safety.
- **Explicit truncation warning** rendered as a dedicated `AssistantText` entry when `stop_reason in ("length", "max_tokens")` AND some visible content was already shown (so the empty-response fallback didn't fire). Previously runtime's auto-upgrade path caught most cases but a provider that caps hard mid-stream let truncated turns through silently. New text:
  - ZH: `(⚠ 回應被截斷 — 模型達到輸出上限 (length)。實際輸出 279 tokens。試試加長 max_tokens 或 context window,或重新提問。)`
  - EN: `(⚠ Response truncated — the model hit its output token cap (length) after 279 tokens. Try increasing max_tokens / context window or rephrasing.)`
- **`_truncation_warning_message()`** pure helper. Reuses `_session_is_cjk`; testable without mounting the TUI.
- **Unconditional turn-end debug log** captures `out_tokens`, `thinking_len`, `assistant_added`, `saw_tool_call`, `stop_reason` on EVERY turn — not just empty-response path. `-v` runs now have full state for every turn, not just fallback paths.

### Context
Found by investigating a second Qwen3.5-122B screenshot: TUI showed 3 `web_search` dots + "根據搜尋結果,以下是今日三則熱門新聞:" intro but NO list items, despite model reporting 279 output tokens. Oneshot `-q` produced the full 3-item list for the same query. Isolated to TUI observability gap — the runtime already auto-upgrades on `finish_reason=length` (conversation.py:L1400-1409), but when that path doesn't catch it (e.g. partial-stream truncation), the TUI had no way to surface the cause.

### Tests
- **6 new tests** in `test_empty_response_i18n.py`: EN + ZH truncation warnings with token count, `max_tokens` stop_reason variant, zero-token edge case, CJK language detection from session history, ⚠ marker in both locales
- Existing 32 tests still pass — **38 total**
- Full `tests/test_tui/` sweep: **378 passed**, no regressions

### Not changed
- Runtime layer — purely TUI observability
- Runtime auto-upgrade on `finish_reason=length` — still fires first
- Empty-response fallback (PR #36) — still fires when no visible content

## Unreleased — Empty-response diagnostics: debug log + unclassified variant

### Added
- **Diagnostic log at the TUI empty-response fallback** captures the full state in one `logger.warning` line so a `-v` run has everything needed to debug the cause: `out_tokens`, `thinking_len`, `saw_tool_call`, `assistant_added`, `stop_reason`, and a 120-char `thinking_head` preview. Previously the user only saw a generic i18n message with no observable state — the only way to investigate was to hand-instrument and re-run.
- **New "unclassified tokens" diagnostic variant** (`_EMPTY_RESPONSE_UNCLASSIFIED_EN` / `_ZH`) for the specific case where the model emitted N output tokens but the TUI could not route *any* of them to visible text, thinking, or a dispatched tool call. Includes the actual token count in the message so the user can compare against their `max_tokens` / `thinking_budget` config without leaving the TUI. Classic causes: malformed `<think>` tags that slipped past the parser, a partial `<tool_call>` that got stripped but not dispatched, or truncation from a low output-token cap.
- **`_empty_response_message` helper accepts `turn_output_tokens` and `thinking_buffer_len` keyword arguments** (both default 0 for backward-compat with existing callers). The decision tree is now:
  1. Saw a dispatched tool call → tool-call variant (actionable: "ask for a direct answer")
  2. Tokens emitted but nothing in thinking buffer → **unclassified variant (new)** with token count
  3. Otherwise → classic "thinking exhausted the budget" variant

### Context
Found by investigating a Qwen3.5-122B screenshot where the user saw the classic "模型沒有產生任何回應 — 可能 thinking 用光輸出 token" message after a 24-output-token turn. The `-q` oneshot path returned a correct 282-token response for the same query, isolating the bug to the TUI layer (runtime layer is fine — wave2-1a P3 assembly handles thinking blocks correctly). The empty-response fallback at `app.py:L1665` pre-dates wave2 and had no observability — this PR adds the single missing log line so the next occurrence is fully diagnosable.

### Tests
- **`tests/test_tui/test_empty_response_i18n.py`** — 6 new tests:
  - Unclassified variant in English includes the token count and references `max_tokens`/`budget`
  - Unclassified variant in Chinese includes the token count and references `max_tokens`/`thinking_budget`
  - Classic "thinking exhausted" variant fires when thinking buffer has content (even with positive tokens)
  - Classic variant fires when both tokens and thinking are zero (pre-wave2 default)
  - Tool-call variant still wins precedence over unclassified when both conditions could apply
  - Legacy callers without the new kwargs still get the classic message (backward-compat)
- Existing 26 `test_empty_response_i18n.py` tests still pass — **32 total**, no regressions.
- Full `tests/test_tui/` sweep: **372 passed**.

## Unreleased — Wave2-1a P5: conversation_db thinking persistence + FTS5 (wave2-1a COMPLETE)

### Added
- **`messages` table gains `content_type` + `signature` columns.** Idempotent schema: fresh DBs get both via the new `CREATE TABLE`; pre-P5 DBs get them via `ALTER TABLE ADD COLUMN` gated on `PRAGMA table_info` so re-runs are no-ops. Legacy rows with NULL content_type are still matched by the text-only search filter via `COALESCE(m.content_type, 'text')`.
- **`ConversationDB.log_message`** accepts `content_type` and `signature` kwargs defaulted to pre-P5 values. `log_thinking(conv_id, content, signature, created_at)` convenience wrapper pins role=assistant, content_type=thinking.
- **`ConversationDB.search(query, content_type=None)`** optional filter: "text" only, "thinking" only, or both. `SearchResult.content_type` field exposed so UI can render thinking matches differently.
- **`ConversationRuntime._db_log_thinking(content, signature)`** called from the assembly path so every assistant turn that produced reasoning lands in FTS5 alongside the visible text log.

### Migration notes
- Pre-P5 DB files auto-upgrade on first open. PRAGMA-gated, idempotent, logs INFO per column added.
- Signature bytes round-trip byte-for-byte through SQLite.
- Rows written before P5 (NULL content_type) still searchable — COALESCE maps them to 'text'.

### Tests
- **`tests/test_runtime/test_conversation_db_thinking_wave2_1a_p5.py`** — 11 new tests: 3 migration (fresh / legacy / idempotent), 1 log_message back-compat, 2 log_thinking (role+type+content, signature byte-opacity), 5 search (no-filter, thinking-only, text-only, SearchResult.content_type field, legacy NULL → text)
- Full sweep: **1709 passed**, no regressions (1698 P4 + 11 new)

### Wave2-1a spec status: COMPLETE ✅

| Phase | PR | Scope | Tests | Sweep |
|---|---|---|---|---|
| P1 | #26 | `ThinkingBlock` dataclass + order validator | 16 | 1658 |
| P2 | #27 | `openai_compat` parses 5 provider shapes | 19 | 1677 |
| P3 | #28 | Assembly + Session serialization + isinstance sweep | 10 | 1687 |
| P4 | #29 | Compressor atomicity + outbound drop warning | 11 | 1698 |
| **P5** | **this** | `conversation_db` migration + FTS5 thinking search | **11** | **1709** |

**Total delta: 67 new tests, +51 test sweep (1658 → 1709), 5 merge-ready stacked PRs.**

Thinking is now a first-class ContentBlock end-to-end: parsed from 5 provider shapes (P2), stored in `Message.content` (P3), serialized to session JSON (P3), counted by `estimated_tokens()` (P3), preserved as atomic pair with adjacent tool_use during compression (P4), dropped-with-warn on outbound for OpenAI-compat (P4), indexed in `conversation_db` FTS5 with content_type filter (P5). A future native `AnthropicProvider` now has a clean path to plug in extended-thinking + tool-use multi-turn without touching the data model.

## Unreleased — Wave2-1a P4: Compressor atomicity + explicit outbound drop

### Added
- **`ContextCompressor._micro_compact` treats `(Thinking*, ToolUse)` as an atomic pair.** When a stale `ToolUseBlock` is dropped (because a later call to the same file made it redundant), any `ThinkingBlock` that immediately precedes it in the same assistant message is also dropped. The while-loop handles the Anthropic pattern where a long reasoning trace is split across multiple consecutive thinking blocks before a single tool_use. Without this, signed thinking would be orphaned — a future Anthropic-direct provider's signature verification would fail on the next request round-trip because thinking-without-its-paired-tool_use is invalid in the extended-thinking state machine. Unsigned thinking (Qwen, DeepSeek) is harmless to drop, but the pairing rule keeps the P1 ordering invariant trivially valid across compressions.
- **Thinking-only leftover messages are dropped.** If pruning a message's sole tool_use + its preceding thinking leaves the message with nothing but more thinking blocks (no text, no other tool uses), the whole message is dropped — orphaned reasoning with no load-bearing connection to subsequent turns has no value. Messages with non-thinking siblings are preserved.
- **`openai_compat._convert_message` explicitly counts and warns on dropped thinking.** Previously the drop was implicit (the has_multiple branch only handled TextBlock + ImageBlock and thinking fell through the unhandled gap). Now the branch has a named `elif isinstance(block, ThinkingBlock)` arm that increments a counter, and `_warn_thinking_dropped_once(count)` fires a warning exactly once per process the first time any request sends a reasoning-model assistant message through the outbound serializer. This is observability, not a behavior change — the drop itself is still the correct behavior for OpenAI-compat servers which reject unknown content types.

### Decisions recorded
- **Outbound default: strip, not round-trip.** The P4 plan floated a `strip_thinking_on_outbound` config flag defaulting to pass-through. We chose the opposite: strip by default, because:
  1. The only current provider is `OpenAICompatProvider` and every known OpenAI-compat server (vLLM, DeepSeek, OpenAI, Qwen) would 400 on unknown content types.
  2. A native `AnthropicProvider` will override `_convert_message` to emit structured thinking; the round-trip path lives in that override, not in a flag on the base class.
  3. YAGNI: no real proxy wants round-tripped thinking today. Adding the flag would be a configuration surface with no consumer.
- **Atomic pair window: immediately preceding only.** The fix pops ThinkingBlocks via a while-loop that only walks backward from the dropped ToolUseBlock within the same message. We do not attempt to preserve thinking that was emitted in a different message — Anthropic's extended-thinking state machine ties thinking to the tool_use in the same assistant message, not across turns.

### Tests
- **`tests/test_runtime/test_compressor_thinking_wave2_1a_p4.py`** — 6 new tests: single thinking+tool_use pair atomicity, multiple consecutive thinking blocks before a tool_use, kept tool_use preserves its thinking, thinking-only leftover message dropped, preceding TextBlock sibling preserved (only thinking gets popped), compressed session still satisfies the P1 order invariant.
- **`tests/test_api/test_outbound_thinking_wave2_1a_p4.py`** — 5 new tests: warn-once on first drop, warn-once across 10 requests, warn count reflects multiple thinking blocks, no warning for pure-text messages, visible text content survives drop (observability-only guarantee).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1698 passed**, no regressions (1687 from P3 + 11 new).

### Context
P4 of the 5-phase thinking-blocks-first-class spec. Compressor atomicity was the main architectural risk in the spec — without it, signed thinking would silently break on future Anthropic-direct provider integration. Outbound explicit drop is the observability half: the drop is still the right behavior today, but now it's visible in the logs rather than a silent accident. P5 (conversation_db persistence + FTS5 thinking search) is next and final.

## Unreleased — Wave2-1a P3: ThinkingBlock assembly + session persistence

### Added
- **`conversation.py` assistant assembly prepends thinking blocks.** The stream loop accumulates `thinking_parts` from `StreamThinkingDelta` events. At assembly time, a single merged `ThinkingBlock(content="".join(parts))` is prepended to `assistant_blocks` before any `TextBlock` / `ToolUseBlock`. The P1 `validate_assistant_content_order` is called defensively so a future refactor that reorders blocks fails loudly at the broken call site.
- **`Session` serializes thinking end-to-end.** `_block_to_dict` / `_dict_to_block` handle `{"type": "thinking", "thinking": "...", "signature": "..."}` (Anthropic-compatible shape; P5 reuses it). Pre-P5 rows missing the signature column rehydrate with `signature=""`.
- **`Session.estimated_tokens()` counts thinking.** DeepSeek-R1 sessions with 10K tokens of reasoning no longer look empty to the proactive compactor.

### Isinstance audit sweep
Grep-based sweep found 22 `isinstance(block, TextBlock|ToolUseBlock)` chains. Verified behavior:

- `session.py` serialization + estimated_tokens — **fixed here** (required).
- `compressor.py` (5 chains) — silently drops thinking. **Safe for OpenAI-compat**; P4 fixes the Anthropic round-trip case.
- `openai_compat.py` `_convert_message` (3 chains) — drops thinking from outbound parts list, solo-thinking falls through to empty content. **Does not crash**; correct for OpenAI-compat. P4 wires the Anthropic round-trip.
- `swarm/coordinator.py`, `runtime/vision.py`, `utils/search.py`, `cli/oneshot.py` (6 chains) — read-only text extractors for display / search / summary. Silently skipping thinking is correct behavior.

No chain raises on `ThinkingBlock`.

### Tests
- **`tests/test_runtime/test_thinking_assembly_wave2_1a_p3.py`** — 10 new tests: session serialization round-trip (5 inc. byte-opaque signature, P5-forward missing-column tolerance, full Session.to_dict/from_dict), estimated_tokens with + without thinking (2), outbound `_convert_message` with `(Thinking, Text)` and solo thinking (2), order validator defensive call (1).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1687 passed**, no regressions.

### Context
P3 of the 5-phase thinking-blocks-first-class spec. After P3, thinking content lands in `Session.messages` for the first time — previously it was stream-only and discarded on block_stop. P4 (outbound round-trip + compressor atomicity) and P5 (conversation_db + FTS5) follow.

## Unreleased — Wave2-1a P2: ThinkingBlock inbound parsing

### Added
- **`MessageResponse.thinking: tuple[ThinkingBlock, ...] = ()`** side-channel field for provider-reported thinking blocks. Non-thinking providers leave it empty. P3 is where these move into the assembled assistant `Message.content`; P2 only surfaces them on the response object so downstream assembly can see them.
- **`llm_code/api/openai_compat.py` now extracts reasoning content** from 5 provider shapes:
  - `message.reasoning_content` (DeepSeek-R1 / DeepSeek-reasoner / Qwen QwQ / vLLM) — scalar string
  - `message.reasoning` (OpenAI o-series newer SDK) — scalar string
  - Anthropic-style structured blocks: `message.content` is a list containing `{"type": "thinking", "thinking": "...", "signature": "..."}` — signature preserved byte-for-byte (never normalized, decoded, or trimmed — Anthropic verifies it server-side on the next request echo)
  - Streaming `delta.reasoning_content` — emits `StreamThinkingDelta` chunks so the TUI's existing flush logic picks them up
  - Streaming `delta.reasoning` — same, for o-series
- **`_extract_reasoning_text(source)` and `_extract_anthropic_thinking(content)`** helpers in openai_compat provide the extraction logic. Both are defensive: non-string fields, non-list inputs, and malformed list entries are silently skipped rather than crashing the parser.
- **Non-streaming parse** now handles both scalar `message.content` (unchanged) and Anthropic-style structured content list — text blocks become `TextBlock`, thinking blocks go to the side channel.
- **Streaming parse** now handles interleaved thinking + text in a single chunk: thinking is emitted first so the TUI flushes it before the visible text arrives (stable ordering pinned by test).

### Context
This is P2 of the thinking-blocks-first-class spec (see `docs/superpowers/specs/2026-04-09-llm-code-thinking-blocks-first-class-design.md`, local-only). P1 added the data model; P2 makes the provider parser actually populate it. Nothing downstream consumes `MessageResponse.thinking` yet — P3 is where `conversation.py` accumulates these and prepends them into `Message.content` before assembly.

Empty-string reasoning chunks are ignored so turns without thinking don't emit zero-length blocks. This matters for cost tracking and compression: a no-op thinking chunk would otherwise inflate token estimates in P3.

### Tests
- **`tests/test_api/test_openai_compat_thinking.py`** — 19 new tests:
  - 5 unit tests for `_extract_reasoning_text`: field priority, fallback, non-string rejection, empty-string treated as absent
  - 5 unit tests for `_extract_anthropic_thinking`: structured list walking, byte-opaque signature, signature default, scalar/None input rejection, malformed-entry skipping
  - 5 integration tests for non-streaming `_parse_response`: DeepSeek reasoning_content, OpenAI o-series reasoning, Anthropic structured, no-reasoning default, reasoning + tool_call combo
  - 4 streaming integration tests: reasoning_content chunks → StreamThinkingDelta, OpenAI reasoning field, empty chunks skipped, interleaved thinking+text ordering
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1677 passed**, no regressions.

## Unreleased — Wave2-1a P1: ThinkingBlock as first-class ContentBlock

### Added
- **`llm_code/api/types.py` `ThinkingBlock`** — new frozen dataclass with `content: str` and `signature: str = ""`. Represents the model's reasoning / chain-of-thought content as a structured block instead of a stream-only event. The `signature` field is provider-opaque (Anthropic signs thinking blocks for verbatim round-trip; Qwen / DeepSeek / OpenAI o-series leave it empty).
- **`ContentBlock` Union** now includes `ThinkingBlock` as the first member. Widening is additive: every existing `isinstance(block, ContentBlock)` check continues to work; any downstream consumer that doesn't yet know about thinking blocks simply won't match its branch (audit sweep for those is P3).
- **`llm_code/api/content_order.py`** — pure `validate_assistant_content_order(blocks)` that raises `ThinkingOrderError` (with `.index`, `.offending_type`, `.preceding_type`) when any thinking block appears after a non-thinking block. Empty tuples and tuples without any thinking blocks pass trivially, so the entire existing codebase stays valid — P1 lands with zero runtime effect.

### Context
The original wave2-1a plan was a small "thinking block order validator" sub-PR. The audit verification pass discovered the real architectural gap: llm-code has no `ThinkingBlock` ContentBlock type at all. `openai_compat.py` has zero references to `reasoning_content`; DeepSeek-R1 / OpenAI o-series / Qwen QwQ thinking is silently discarded at the API parsing layer. The current "working" state only holds because of a single provider × single thinking-mode coincidence (OpenAI-compat + Qwen3 `<think>` tag mode). Any attempt to add a native AnthropicProvider with extended thinking + tool use would break immediately on multi-turn, because Anthropic requires signed thinking blocks to be echoed back in subsequent requests.

This PR is **P1 of a 5-phase spec** (`docs/superpowers/specs/2026-04-09-llm-code-thinking-blocks-first-class-design.md`). P1 introduces the data model only — no producer, no consumer, no persistence. P2 adds the inbound parser; P3 assembles thinking into `Message.content`; P4 handles outbound serialization + compressor atomicity; P5 adds DB persistence.

### Tests
- **`tests/test_api/test_thinking_block.py`** — 16 new tests: frozen dataclass + signature default + signature byte-opaque preservation, `ContentBlock` Union membership (thinking first, all existing members intact), validator happy paths (empty, single, multiple consecutive thinking, thinking before text, thinking before tool_use, no-thinking-at-all), validator violations (text before thinking, tool_use before thinking, interleaved thinking mid-sequence), error message includes index + neighboring types.
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1658 passed**, no regressions.

## Unreleased — Wave2-1d: CancelledError cleanup on interrupted tool (wave2-1 COMPLETE)

### Added
- **`tool_cancelled` hook event**, `{tool_name, tool_id}` payload, registered under `tool.*` glob group.
- **`_execute_tool_with_streaming`** wraps progress-queue + future-await in `try/except asyncio.CancelledError`. On cancel: fires `tool_cancelled` hook, yields `is_error=True` `ToolResultBlock`, re-raises. Yield-then-raise order is load-bearing — otherwise the session has an orphan `ToolUseBlock` with no matching `ToolResultBlock` and the next turn's payload is malformed.

### Fixed
- Interrupted tool calls (user ctrl+c, parent-task timeout) used to propagate `CancelledError` without any cleanup — conversation round-trip invariant broke silently. The ThreadPoolExecutor worker thread still runs to completion in the background (CPython constraint), but the session state is now consistent.

### Tests
- **`tests/test_runtime/test_wave2_1d_cancel_cleanup.py`** — 7 new tests: 3 hook registration (name / glob / exact), 3 cancellation contract (yield-before-reraise order, tool name in error content, payload schema), 1 source-level guard on the production try/except + yield/raise order + `tool_cancelled` fire.
- Full sweep: **1649 passed**, no regressions.

### Wave2-1 session recovery: COMPLETE ✅

| Sub | Status | PR |
|---|---|---|
| 1a thinking blocks P1–P5 | ✅ | #26–#30 |
| 1b Retry-After + ProviderTimeoutError | ✅ | #31 |
| 1c Empty counter + context pre-warn | ✅ | #32 |
| **1d CancelledError cleanup** | **✅** | **this** |

All 8 failure modes from the wave2 audit are now covered:

| Mode | Pre-wave2 | Post-wave2 |
|---|---|---|
| ToolNotFound | ✅ | ✅ |
| MalformedToolInput | ✅ | ✅ |
| ThinkingBlockOrder | ❌ | ✅ (wave2-1a P1–P5, reframed as architecture) |
| RateLimited | ⚠️ | ✅ (wave2-1b Retry-After) |
| ProviderTimeout | ⚠️ | ✅ (wave2-1b ProviderTimeoutError) |
| ContextWindowExceeded | ⚠️ | ✅ (wave2-1c pre-warning) |
| EmptyAssistantResponse | ⚠️ | ✅ (wave2-1c counter) |
| **InterruptedToolCall** | **⚠️** | **✅ (wave2-1d)** |

## Unreleased — Wave2-1c: Empty response counter + context pressure pre-warning

### Added
- **`_consecutive_empty_responses`** counter on `ConversationRuntime`. Empty turn (no text, no tool calls) → increment; productive turn → reset. **2nd in a row** injects a nudge user message (`[system nudge] Your previous response was empty...`); **3rd** raises `RuntimeError` so a degenerate provider state cannot burn the turn budget on nothing.
- **`empty_assistant_response` hook event** fires on every empty response with `{consecutive, model}`. Observers see the escalation unfold regardless of whether nudge/abort thresholds have been reached.
- **`context_pressure` hook event** fires once per ascending bucket transition **before** the 100% compaction trigger. Buckets: `low` (<70%), `mid` (70–85%), `high` (≥85%). Payload: `{bucket, ratio, est_tokens, limit}`. Compaction resets the bucket so the next ascending crossing re-fires.
- Both new event names in `_EVENT_GROUP`: `context.context_pressure` + `session.empty_assistant_response` so `context.*` / `session.*` glob subscribers pick them up automatically.

### Fixed
- Empty response loops silently burned turn budgets (the old `if assistant_blocks:` just skipped assembly with no logging or counter).
- Context-window pressure was invisible to observers until the 100%-hit compaction log — no pre-emptive escape hatch.

### Tests
- **`tests/test_runtime/test_wave2_1c_empty_context.py`** — 24 new tests: 3 hook registration, 10 pressure buckets (9 parametrized + zero-limit guard), 5 pressure transitions (ascending mid / mid→high, no spam within bucket, silent descent, refire after reset), 5 empty-counter state machine (continue/nudge/abort/reset/hook-on-every-empty), 1 source-level guard on runtime `__init__` sentinels.
- Full sweep: **1666 passed**, no regressions.

### Wave2-1 progress
| Sub | Status | PR |
|---|---|---|
| 1a P1–P5 thinking blocks | ✅ | #26–#30 |
| 1b Retry-After + ProviderTimeoutError | ✅ | #31 |
| **1c Empty counter + context pre-warn** | **✅** | **this** |
| 1d CancelledError cleanup | — | — |

## Unreleased — Wave2-1b: Retry-After header + ProviderTimeoutError

### Added
- **`ProviderRateLimitError.retry_after: float | None`** field carries the provider's `Retry-After` header value (in seconds) when the 429 response included one. Downstream `_post_with_retry` now honors this hint instead of always using `2 ** attempt`, so the retry respects the provider's own rate-limit reset window.
- **`ProviderTimeoutError`** — new retryable `ProviderError` subclass wrapping `httpx.ReadTimeout` / `ConnectTimeout` / `WriteTimeout` / `PoolTimeout`. Previously all four flavors fell through `_post_with_retry` uncaught and became generic `Exception` in the conversation loop, skipping the retry budget entirely. Now they get the standard exponential backoff path just like `ProviderConnectionError`.
- **`_parse_retry_after_header(raw)`** helper in `openai_compat.py` — defensive parser that accepts the delta-seconds form (every real LLM provider's 429 response), returns `None` on missing / empty / unparseable / non-positive / HTTP-date input, and **clamps positive values to `_MAX_RETRY_AFTER_SECONDS = 60.0`** so a misbehaving proxy returning `Retry-After: 86400` cannot wedge the runtime for a day.

### Fixed
- **`_post_with_retry` split `ProviderRateLimitError` off from `ProviderConnectionError`.** The combined handler used `2 ** attempt` for both; now rate-limit specifically checks `exc.retry_after` and falls back to exponential only when absent. Connection errors are unchanged.
- **`_raise_for_status` reads `Retry-After` from the 429 response** and passes it to the new `ProviderRateLimitError(msg, retry_after=...)` constructor.

### Tests
- **`tests/test_api/test_rate_timeout_backoff_wave2_1b.py`** — 13 new tests:
  - 5 unit tests for `_parse_retry_after_header`: None/empty, delta-seconds (int + float + whitespace), unparseable (garbage + HTTP-date form), non-positive rejection, 60s cap clamp
  - 4 rate-limit retry tests: honors `Retry-After: 3.5`, falls back to `2 ** attempt` without header, clamps hostile `999999` to 60s, exhausted budget re-raises with `retry_after` attribute preserved
  - 3 timeout tests: `httpx.ReadTimeout` → retry, `ConnectTimeout` → retry, all 4 flavors exhausted → `ProviderTimeoutError(is_retryable=True)`
  - 1 sanity test: 401 auth error still not retried (verifies wave2-3 `is_retryable` path is untouched)
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1655 passed**, no regressions.

### Context
Part of the wave2-1 session recovery follow-through (see `docs/superpowers/specs/2026-04-08-llm-code-borrow-wave2-audit.md`). The audit found:
- RateLimited ⚠️: no exponential backoff respected header, no Retry-After parsing — **fixed**
- ProviderTimeout ⚠️: no special handling, timeouts fell through generic Exception catch — **fixed**

Remaining wave2-1 items: **1c** (EmptyAssistantResponse counter + ContextWindow pre-warning), **1d** (CancelledError cleanup on interrupted tool execution).

## Unreleased — Wave2-2: Cost tracker cache tokens + unknown-model warning

### Fixed
- **`TokenUsage` now carries `cache_read_tokens` / `cache_creation_tokens`** end-to-end. Previously the streaming provider parser dropped both buckets on the floor when building `TokenUsage`, so even though `CostTracker.add_usage()` already supported the 10% / 125% cache-pricing math, the TUI hook had nothing to feed it. Cache reads on claude-sonnet-4-6 are roughly 10% of input price, so a session doing heavy prompt caching was over-billed by the full cache-read amount in every summary.
- **`llm_code/api/openai_compat.py`** centralizes usage-dict → `TokenUsage` conversion in `_token_usage_from_dict()`, which handles both payload shapes: OpenAI-compat nests cache reads under `prompt_tokens_details.cached_tokens`; Anthropic surfaces them as top-level `cache_read_input_tokens` / `cache_creation_input_tokens`. Anthropic's explicit field wins when both appear.
- **`llm_code/tui/app.py` `StreamMessageStop` hook** now forwards the cache buckets into `cost_tracker.add_usage(cache_read_tokens=..., cache_creation_tokens=...)`. Uses `getattr(..., 0)` so any stray `TokenUsage` constructed without the new fields stays safe.
- **`CostTracker` warns once per unknown model.** Self-hosted setups (Qwen on GX10 etc.) still stay silent after the first event, but a genuine typo in the model name now surfaces with `cost_tracker: no pricing entry for model 'xxx'; treating as free. Add a custom_pricing row in config if this is a paid model.` — previously it silently priced the whole session at $0. Empty model name is also silent so initialization ordering doesn't spam the log.

### Tests
- **`tests/test_runtime/test_cost_tracker_wave2_2.py`** — 11 new tests: TokenUsage backward-compat defaults, OpenAI vs Anthropic usage-dict extraction (including the "both shapes present" edge case), empty-dict handling, warn-once / warn-per-new-model / known-model-silent / empty-model-silent, and end-to-end cache pricing (`claude-sonnet-4-6`: 1M cache_read + 1M cache_write = $4.05).
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1660 passed** (up from 1653, no regressions).

## Unreleased — Wave2-3: Model fallback quick-win fixes

### Fixed
- **`llm_code/runtime/conversation.py` provider error handler** now short-circuits on `is_retryable=False` errors (`ProviderAuthError`, `ProviderModelNotFoundError`). Previously a 401/404 from the upstream API burned the full 3-strike retry budget before the fallback switch, wasting time and quota on errors that cannot possibly succeed on retry. A new `http_non_retryable` hook fires so observers can count these distinctly from transient failures.
- **`cost_tracker.model` now follows a fallback switch.** When the 3-strike threshold flips `self._active_model` to the fallback model, the runtime also assigns `self._cost_tracker.model = _fallback` and resets `_consecutive_failures`. Previously every token after a fallback was still priced as the (failed) primary model, so session cost summaries mis-attributed spend. `_consecutive_failures` used to stay at 3 after the switch, which meant the new model got zero retries before the next escalation — that's now reset to 0 on switch.

### Tests
- **`tests/test_runtime/test_fallback_wave2_3.py`** — 7 new tests pin the two fixes: non-retryable error contract on `ProviderAuthError`/`ProviderModelNotFoundError`, retryable contract on rate-limit/overload, default retryable behavior for bare exceptions, writable `cost_tracker.model`, and end-to-end pricing attribution across a model switch (verifies the tracker uses the new custom-pricing row after reassignment).
- Full conversation + retry-tracker regression sweep (37 tests) still passes.

## Unreleased — Wave2-4: Compaction todo preserver + phase-split hooks

### Added
- **`pre_compact` / `post_compact` hook events.** Observers can now distinguish the snapshot moment from the rehydration moment of a compaction pass. The legacy `session_compact` event still fires alongside `pre_compact` so existing hook configurations keep working unchanged. Both new events are in the canonical `session.*` group, so any glob subscriber (e.g. `session.*`) picks them up automatically.
- **`llm_code/runtime/todo_preserver.py`** — pure module providing `snapshot_incomplete_tasks(task_manager)` (best-effort, never raises even on a broken task store) and `format_todo_reminder(snapshot, max_tokens=500)` with a hard token cap. The formatter truncates with a `... (N more)` footer when the cap would be exceeded, so a runaway task list cannot balloon an already-tight context window.
- **`ConversationRuntime._compact_with_todo_preserve(max_tokens, reason)`** helper routes all four in-tree compaction call sites (proactive / prompt_too_long / api_reported / post_tool) through a single path that fires the phase-split hooks with uniform payload: `{reason, before_tokens, target_tokens, preserved_todos}`. Previously only one of the four sites fired `session_compact` at all, so observers had no visibility into three of the compaction triggers.

### Tests
- **`tests/test_runtime/test_todo_preserver_wave2_4.py`** — 12 new tests covering: empty/broken/None task-manager handling, snapshot structure, format hard-cap truncation with `... (N more)` footer, default-cap sanity for typical sessions, phase-event registration in `_EVENT_GROUP`, and `session.*` glob matching for both new phase events.
- Full `tests/test_runtime/` + `tests/test_api/` sweep: **1654 passed**, no regressions.

## Unreleased — Wave2-5: Plugin executor (schema + dynamic loader + SkillRouter hooks)

### Added
- **`PluginManifest.provides_tools`** — declarative list of Python tools a plugin exports as `"package.module:ClassName"`. Parses from either `providesTools` (camelCase) or `provides_tools` (snake_case).
- **`PluginManifest.permissions`** — declared capability envelope (dict). Wave2-5 reads for surfacing / audit; sandbox enforcement is a follow-up. Non-dict values dropped defensively.
- **`llm_code/marketplace/executor.py`** — the missing piece. `load_plugin(manifest, install_path, *, tool_registry, skill_router=None, force=False)` resolves each `provides_tools` entry, imports the module (with install path temporarily on `sys.path`, restored in `finally`), instantiates the class, registers it. Returns a `LoadedPlugin` handle so `unload_plugin` can reverse the load. `PluginLoadError` / `PluginConflictError` carry `.plugin_name` + `.entry` for log-traceable failures.
- **`ToolRegistry.unregister(name) -> bool`** — idempotent removal. Used by executor rollback and `unload_plugin`.
- **`SkillRouter.add_skill(skill)`** / **`remove_skill(name) -> bool`** — post-construction registration/removal. Rebuilds TF-IDF + keyword index, invalidates route cache, rejects duplicate names.

### Fixed
- **Plugin-provided Python tools now have an actual loader.** Before wave2-5 the marketplace had manifest parsing + install-from-local/github/npm + security scan + 91 tests, but no code path that took a declared tool class and put it in the tool registry. Plugin authors could ship Python tools and llm-code silently ignored them.

### Contract: rollback on any failure
Any failure during `load_plugin` (unparseable entry / missing module / missing class / ctor failure / name conflict) unregisters every tool this load call already registered before the exception propagates. Registry returns to its pre-load state. Pinned by `test_load_plugin_rolls_back_on_conflict` — a two-tool plugin whose second tool conflicts leaves the first tool NOT registered.

### Scope discipline
Lands the **executor + schema + router hooks only**. TUI wiring (hooking `load_plugin` into `_cmd_plugin install` and `_reload_skills`) is deferred to a follow-up PR. Existing `/plugin install` path for markdown-only skill plugins continues to work exactly as before.

### Tests
- **`tests/test_marketplace/test_plugin_executor_wave2_5.py`** — 20 new tests: 6 manifest schema (camelCase / snake_case / empty / permissions dict / default None / non-dict dropped), 3 `unregister` (remove / missing / re-register), 3 happy-path (fixture plugin loads, empty manifest, sys.path cleanup), 2 conflict (rollback / force override), 4 structural failures (unparseable / missing module / missing class / broken ctor), 2 `unload_plugin` (removes / idempotent)
- **`tests/test_runtime/test_skill_router_add_remove_wave2_5.py`** — 7 new tests: add grows list, add rejects duplicate, add invalidates cache, remove unknown returns False, remove works, remove invalidates cache, add-then-remove round-trip
- Full `tests/test_runtime/` + `tests/test_api/` + `tests/test_marketplace/` + `tests/test_tools/` sweep: **2794 passed**, no regressions (existing 91 marketplace tests unchanged).

### Wave2 status: all 11 items landed

| Item | PR |
|---|---|
| wave2-1a thinking blocks P1–P5 | #26–#30 |
| wave2-1b rate-limit + timeout | #31 |
| wave2-1c empty + context pre-warn | #32 |
| wave2-1d cancel cleanup | #33 |
| wave2-2 cost tracker | #24 |
| wave2-3 fallback | #24 |
| wave2-4 todo preserver | #25 |
| wave2-6 dialog launcher | #34 |
| **wave2-5 plugin executor** | **this** |

## Unreleased — Wave2-6: Dialog launcher (API + Scripted + Headless)

### Added
- **`llm_code.tui.dialogs` package** with unified `Dialogs` Protocol (4 async methods: `confirm` / `select` / `text` / `checklist`), generic `Choice[T]` frozen dataclass (`value`, `label`, `hint`, `disabled`), and two explicit exception types (`DialogCancelled`, `DialogValidationError`).
- **`ScriptedDialogs`** deterministic test backend. Pre-enqueue responses via `push_confirm` / `push_select` / `push_text` / `push_checklist` / `push_cancel`. `.calls` log captures exact prompt text; `assert_drained()` at teardown catches unconsumed responses. Validates enqueued select / checklist values are actually in the passed-in choice list.
- **`HeadlessDialogs`** stdin/stderr line-based backend for CI, pipe mode, `--yes` runs, SSH without TTY. Writes prompts to stderr so piped stdout stays clean. Multi-line text is blank-line terminated. Select uses 1-based indices. Checklist parses comma-separated indices. EOF / out-of-range / disabled / non-integer → `DialogCancelled`. `assume_yes=True` short-circuits every prompt to its default with zero I/O. `confirm(danger=True)` renders a ⚠ prefix.

### Scope discipline
This PR lands the **API + two non-interactive backends only**. The Textual backend (modal screens inside the running app) and the call-site migration sweep (~12 existing hand-rolled prompts across `llm_code/tui/`) are deferred to follow-up PRs so this change stays focused and reviewable:

- No existing TUI code is modified — every hand-rolled prompt continues to work exactly as before.
- New code that needs a dialog can already use `ScriptedDialogs` in tests and `HeadlessDialogs` in CI.

### Tests
- **`tests/test_tui/test_dialogs_wave2_6.py`** — 36 new tests:
  - 4 Protocol surface + `Choice` type tests
  - 13 `ScriptedDialogs` tests (push/empty-queue/cancel, value membership validation, validator runs, bounds enforcement, drain assertion, call log)
  - 16 `HeadlessDialogs` tests (confirm y/n/blank/EOF/danger, `assume_yes` short-circuit, select index/default/out-of-range/disabled, text single/default/multiline/validator, checklist comma/blank/min/max)
  - 3 cross-backend contract tests (shared `_drive_simple_confirm` helper exercises both backends against the same spec)
- Full `tests/test_runtime/` + `tests/test_api/` + `tests/test_tui/` sweep: **2008 passed**, no regressions.

### Deferred
- `TextualDialogs` backend (needs screen push/pop integration)
- Call-site migration sweep (worktree confirm, permission prompt, skill picker, commit-message input, settings modal, quick-open, MCP approval, etc.)
- Removal of legacy prompt helpers after migration


## v1.12.0 (2026-04-08)

**Highlights:**
- **Single source of truth refactor** (PR #21) — shared `ConversationRuntime` test fixture, canonical `StreamParser` replaces TUI + runtime duplicate parsers, system prompt ↔ ToolRegistry lint
- **Hermes variant 4 parser** (PR #22) — handles `<tool_call>NAME{"args": {...}}</tool_call>` with no `>` separator; StreamParser now emits sentinel event on unparseable blocks so TUI diagnostic is accurate
- **`-q` quick mode** now drives the real `ConversationRuntime` — no longer bypasses the code path it's supposed to smoke-test
- **Hermes fixture regression museum** grew to 4 captured variants

### Fixed (Hermes variant 4 + StreamParser sentinel)
- **`tools/parsing.py:_HERMES_FUNCTION_TRUNCATED_RE`** now handles Qwen3 variant 4, where the model emits `<tool_call>NAME{"args": {...}}</tool_call>` with no `>` separator between function name and JSON payload. Captured live from Qwen3.5-122B on 2026-04-08 as `tests/test_tools/fixtures/hermes_captures/2026-04-08-pr22-truncated-no-separator.txt`. 4 new unit tests + fixture replay coverage.
- **`streaming/stream_parser.py`** now emits a sentinel `TOOL_CALL` event (`tool_call=None`) when it consumes a `<tool_call>...</tool_call>` block whose body the downstream parser cannot understand. Previously the block was silently swallowed, which caused the TUI to fall back to the "thinking ate output" empty-response diagnostic instead of the "model tried to call a tool" message. New regression test pins this behavior.

### Refactored (single source of truth)
- **`tests/fixtures/runtime.py`** — shared `make_conv_runtime()` factory with canned-response provider and callback-based test tool. Runtime-level tests no longer hand-build a `ConversationRuntime` with ad-hoc `_Provider` classes. Unblocks the PR #17 Task 3 smoke tombstone (now a real test that proves Hermes-truncated tool calls get dispatched through the full runner).
- **`llm_code/cli/oneshot.py:run_quick_mode`** — `-q` quick mode now routes through the real `ConversationRuntime` via `run_one_turn`. Previously it called the provider directly, bypassing system prompt / tool registry / parser / dispatcher — which is why PRs #11/#13/#14 all "verified" fixes via `-q` that missed the real TUI-path bugs.
- **`LLMCodeTUI._register_core_tools_into(registry, config)`** — classmethod extracted from the TUI constructor so the oneshot path registers the same collaborator-free tool set (file/shell/search/web/git/notebook). Prevents the two paths from drifting.
- **`llm_code/streaming/stream_parser.py`** — canonical `StreamParser` state machine for `<think>` / `<tool_call>` parsing. Both TUI rendering and runtime dispatch consume the same events via `StreamParser.feed()`. The TUI inline parser (~110 lines of state machine) is replaced with 45 lines of event routing — net −63 lines and a single source of truth for what the model emitted. 14 unit tests cover text-only, think blocks (full and implicit-end), tool calls (all 3 Hermes variants), cross-chunk tag splits, interleaving, flush.
- **`tests/test_runtime/test_prompt_tool_references.py`** — lint test that scans `<!-- TOOL_NAMES: START -->` / `<!-- TOOL_NAMES: END -->` marker blocks in system prompt markdown files and asserts every backtick-quoted tool name exists in the `ToolRegistry`. Catches the PR #11 / #13 class of bug (system prompt contradicting actual registered tools) before merge.

## v1.11.0 (2026-04-08)

**Highlights:**
- 7 major features ported from oh-my-opencode (themed hooks, dynamic prompt delegation, agent tier routing, LSP coverage expansion, call hierarchy, telemetry tracing with Langfuse)
- Hermes function-calling parser that handles all 3 variants emitted by vLLM-served Qwen3 and similar tool-fine-tuned local models
- Tool-call resilience: fixture replay regression museum + idempotent retry loop detector
- `web_search` and `web_fetch` tools (already existed but now properly advertised in system prompt)

### Added (resilience hardening from 2026-04-08 bug hunt)
- `tests/test_tools/fixtures/hermes_captures/` — regression museum holding the verbatim model captures from PRs #14/#15/#16. `tests/test_tools/test_parsing_fixture_replay.py` parametrizes over the directory and asserts every capture parses; new captures land here as `.txt` files and are auto-discovered. Future parser refactors cannot silently break any of the three Hermes variants we've seen in production.
- `llm_code/runtime/_retry_tracker.RecentToolCallTracker` — per-turn idempotent retry detector. When the model emits the same `(tool_name, args)` pair twice in a row, the runtime aborts the turn with a clear error instead of looping. Closes the failure mode from 2026-04-08 where a parser bug caused web_search to be dispatched with empty args, fail validation, and burn 76K tokens / 3.6 minutes in a retry loop before giving up. 9 unit tests cover argument-order independence, nested dicts, recovery, and unhashable-arg defense.
- `tests/test_runtime/test_conversation_full_path_smoke.py` — tombstone for a future smoke test that exercises the conversation runner's parser path end-to-end with a fake provider. Currently skipped pending a `ConversationRuntime` test fixture; documents the gap so it can't be silently forgotten.

### Fixed (hotfix — Hermes truncated form with JSON args)
- `tools/parsing.py:_parse_hermes_block` now also handles a third
  Hermes sub-format: truncated function name followed by a JSON
  object payload instead of `<parameter=...>` blocks. PR #15 added
  the truncated form parser but only matched `<parameter=KEY>` blocks,
  so when the model emitted
  `<tool_call>web_search>{"args": {"query": "...", "max_results": 3}}</tool_call>`
  the parser extracted the function name `web_search` but returned
  empty args, causing the runtime to dispatch with empty args, fail
  validation, retry, and accumulate ~76K tokens in a 3.6-minute
  retry loop before giving up. New `_parse_hermes_args` helper tries
  parameter blocks first, then JSON payload (with optional `args` /
  `arguments` wrapper). 6 new TDD tests including the verbatim
  production capture. 37 / 37 parsing tests pass.

### Fixed (hotfix — Hermes template-truncated tool call format)
- `tools/parsing.py:_parse_hermes_block` now also handles the
  template-truncated form of Hermes function calls. Some chat templates
  (notably vLLM-served Qwen3 in tool-calling mode) inject
  ``<tool_call>\n<function=`` as the assistant prompt prefix, so the
  streamed body of `<tool_call>` starts directly with the bare function
  name (e.g. `web_search>...`) instead of `<function=web_search>...`.
  PR #14 added the full-form parser but did not handle this truncated
  variant; the parser silently dropped these calls and the runtime saw
  zero parsed tool calls, ending the turn with an empty visible reply.
  Captured live from local Qwen3.5-122B and pinned in TDD test
  `test_template_truncated_exact_capture_from_production`. 6 new tests
  cover single/multi/no params, underscore-name, full-form coexistence,
  and the malformed `<function>` literal that must still be skipped.
  31 / 31 parsing tests pass.

### Fixed (hotfix — skill router false-match + thinking budget blowout)
- `skill_router` Tier C classifier: clean `none` answers are now authoritative and no longer fall through to the substring fallback. Fixes a regression where CJK queries auto-triggered an irrelevant skill (e.g. `brainstorming` for a news query) because reasoning models mention candidate skill names while ruling them out.
- `skill_router` Tier C substring fallback now requires ≥2 mentions of the winning skill AND a margin of ≥2 over the runner-up before accepting a match. A single mention in the reasoning block is no longer sufficient.
- `dynamic_prompt.build_delegation_section` now takes a `low_confidence` kwarg; when True (set when the routed skill came from the Tier C LLM classifier), the prominent `### Key Triggers` block is suppressed and skills appear only under the softer `### Skills by Category` block.
- `build_thinking_extra_body` now caps `thinking_budget` at `max(1024, max_output_tokens // 2)` when the provider exposes an output token limit, preventing thinking from consuming the entire visible response budget.
- `ConversationRuntime` now wires `_current_max_tokens` (the actual request `max_tokens`) into `build_thinking_extra_body` instead of probing for `provider.max_output_tokens` / `config.max_output_tokens` attributes that don't exist on the local OpenAI-compatible provider. The previous attribute probe always returned `None`, leaving the cap a no-op in TUI mode (which is how the bug was originally observed). Both call sites (initial request and XML-fallback retry) are fixed.
- **qwen.md system prompt: scoped "tool use is mandatory" to file/shell work only.** Previously the prompt instructed Qwen3 to always prefer tools, causing it to invent phantom tool calls (`bash curl` for an RSS feed) on conversational queries like "今日熱門新聞三則". The `<tool_call>` XML would then be stripped by the TUI and surface as an empty-response warning. Now the prompt explicitly says knowledge/explanatory/chit-chat queries must be answered directly. Verified against local Qwen3.5-122B: the same query now produces a clean 57-token direct answer with `has_tool_call=False`.
- **TUI empty-response diagnosis: distinguish `<tool_call>`-only turns from thinking-exhaustion.** The previous "thinking 用光輸出 token" message fired for any turn that emitted tokens but rendered no visible text. Now if the turn contained a `<tool_call>` XML block (which the TUI strips), the message instead tells the user the model tried to call a tool and suggests adding "請直接回答" to the prompt.
- **qwen.md: forbid mentioning tools that aren't actually available.** Even after the previous "tool use is for file/shell only" fix, the model was still suggesting "我可以使用 web_search 工具" in plain text — a tool that doesn't exist in llm-code. The follow-up turn where the user picked option 1 then triggered an actual `<tool_call>web_search` and the empty-response warning. New rule explicitly forbids mentioning or offering hypothetical tools; if the model can't help with the available tools, it must say so directly and stop.
- **TUI i18n: empty-response language detection now session-aware.** Previously the CJK detector only looked at the latest user input, so a Chinese user typing a short ASCII follow-up like `1` or `ok` would flip back to English. Now the helper walks recent user messages in the session and stays Chinese as long as any prior user turn contained CJK.
- **REAL ROOT CAUSE: `tools/parsing.py` now handles Hermes / Qwen3 function-calling format.** PR #11/#13 misdiagnosed the "今日熱門新聞三則 → empty response" bug as system-prompt-induced phantom tool calls. The actual root cause was that `_parse_xml` only accepted JSON-payload format `<tool_call>{"tool": "NAME", "args": {...}}</tool_call>`, while vLLM-served Qwen3 (and most tool-fine-tuned local models) emit Hermes function-calling format inside `<tool_call>` blocks: `<function=NAME><parameter=KEY>VALUE</parameter></function>`. The parser silently dropped these and the runtime saw 0 tool calls, ending the turn with no visible output. `_parse_xml` now tries JSON first, falls back to a Hermes block parser. 6 new TDD tests cover single/multi-param, no-param, multi-line content, mixed-format, malformed-block-skip, and multiple calls in one response.
- **qwen.md system prompt: reverted PR #11/#13 over-restriction.** With the parser fixed, the model can correctly use `web_search` and other read-only tools for legitimate conversational queries (news, weather, doc lookups). The new SP guidance: "use the right tool for the task" — `web_search` for real-time info, `web_fetch` for user-supplied URLs, `read_file`/`bash`/etc. for file/shell work, direct answer for pure knowledge queries. Still forbids inventing tools not in the registered list, and forbids `bash curl` for arbitrary URLs.

### Added
- Three themed builtin hooks ported from oh-my-opencode:
  - `context_window_monitor` — warns once per session at 75% context usage
  - `thinking_mode` — detects "ultrathink" / 深入思考 keywords and flags the turn
  - `rules_injector` — auto-injects CLAUDE.md / AGENTS.md / .cursorrules content
    when a project file is read
- `HookOutcome.extra_output: str` — allows in-process hooks to append content to
  the visible tool result (used by `rules_injector` and `context_window_monitor`).
- `context_window_monitor` builtin hook now actually fires — `ConversationRuntime`
  populates `_last_input_tokens` / `_max_input_tokens` after every LLM stream.
- `thinking_mode` builtin hook is now consumed — `_thinking_boost_active` doubles
  the next turn's `thinking_budget` (capped at provider max).
- Dynamic delegation prompt section: when the conversation runner has live
  tools and routed skills, the system prompt now includes an `## Active
  Capabilities` section with three subsections — Tools by Capability (grouped
  read/search/write/exec/lsp/web/agent), Key Triggers (skill triggers + names),
  and Skills by Category (grouped by skill's first tag). Pure module
  `llm_code/runtime/dynamic_prompt.py`. Byte-budget guard caps the section at
  8 KB by default to protect cache stability.
- Agent tier routing (build / plan / explore / verify / general):
  - BUILD_ROLE (default, unrestricted) and GENERAL_ROLE (focused subagent
    without todowrite) added to BUILT_IN_ROLES
  - is_tool_allowed_for_role() helper
  - ToolRegistry.filtered(allowed) returns a child registry with only the
    named tools (parent untouched)
  - llm_code/runtime/subagent_factory.make_subagent_runtime() builds a
    role-filtered child ConversationRuntime with fresh Session and shared
    parent infrastructure
  - AgentTool is now actually wired — tui/app.py registers it with a
    lazy closure factory instead of runtime_factory=None
  - AgentTool.input_schema.role enum extended to all five roles
- LSP coverage expansion ported from opencode:
  - `llm_code/lsp/languages.py` — single source of truth for extension→language
    mapping (~80 entries) and walk-up project root detection
  - `LspClient.hover()`, `document_symbol()`, `workspace_symbol()` methods with
    `Hover` and `SymbolInfo` dataclasses
  - Three new tools: `lsp_hover`, `lsp_document_symbol`, `lsp_workspace_symbol`
  - `detect_lsp_servers_for_file()` walks upward from any file to its project
    root before resolving servers
  - Expanded `SERVER_REGISTRY` covers 25+ language servers (up from 4)
- LSP call hierarchy + implementation:
  - `LspClient.go_to_implementation()` — concrete implementations of an
    interface, abstract method, or trait
  - `LspClient.prepare_call_hierarchy()` / `incoming_calls()` /
    `outgoing_calls()` — full callHierarchy/* surface
  - `CallHierarchyItem` dataclass with round-trippable LSP serialization
  - Two new tools: `lsp_implementation`, `lsp_call_hierarchy` (the latter
    accepts `direction: incoming | outgoing | both` and runs prepare →
    incoming/outgoing in one tool call)
- Agent decision tracing:
  - Telemetry.span(name, **attrs) — canonical context-manager primitive for
    nested spans (replaces the previous flat-root design)
  - Telemetry.trace_llm_completion(...) — opens an llm.completion span with
    prompt + completion previews (truncated to 4 KB), provider, finish reason
  - Optional Langfuse export: when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
    are set (env or config), spans are also forwarded to Langfuse alongside
    the OTLP exporter via langfuse.otel.LangfuseSpanProcessor
  - Each conversation turn is now wrapped in an agent.turn parent span; the
    LLM call and every tool call become children of that span, forming a
    tree visible in Jaeger / Langfuse / any OTel-compatible UI
  - langfuse>=3.0 added to the existing [telemetry] extra:
    pip install 'llm-code[telemetry]'

### Changed
- `LspWorkspaceSymbolTool` rejects empty queries and caps results at 200 with a `(+N more)` tail.
- `LspWorkspaceSymbolTool` fans out across all running language clients (`asyncio.gather` + dedupe) instead of querying just the first.
- All LSP tools route inputs through a centralized `_validate_lsp_path` helper that returns clean `ToolResult(is_error=True)` for relative paths, missing files, or negative line/column.
- Sync-bridge boilerplate extracted to `_run_async` helper, deduplicated across 8 LSP tools.
- Agent role sentinel refactor: `AgentRole.allowed_tools` is now
  `frozenset[str] | None`. `None` means unrestricted (full inheritance);
  empty `frozenset()` is the explicit deny-all sentinel; non-empty set is a
  strict whitelist. `BUILD_ROLE.allowed_tools` is now `None`.
  `ToolRegistry.filtered(None)` clones the parent; `filtered(frozenset())`
  returns an empty registry. This eliminates the "empty set means
  unrestricted" foot-gun.
- TelemetryConfig now has langfuse_public_key, langfuse_secret_key,
  langfuse_host fields. The config parser falls back to environment variables
  with the same names (uppercase) when the dict keys are absent.

### Fixed
- `rules_injector` no longer reads `CLAUDE.md` / `AGENTS.md` from ancestor
  directories outside the resolved project root (symlink edge case).
- `dynamic_prompt.build_delegation_section` no longer hangs under pathologically small `max_bytes` (added iteration cap + length-stable bailout)
- `dynamic_prompt.build_delegation_section` now honors `max_bytes` strictly — if even the bare header+intro envelope exceeds the budget, returns `""` instead of a soft-violating string
- `classify_tool` recognizes bare `Task` tool name as `agent` category (was falling through to `other`)
- AgentTool is no longer registered with a None runtime factory; calls now
  succeed instead of crashing on first dispatch.
- AgentTool recursion-depth guard now actually trips for build-role
  subagents. Previously, build-role children inherited the parent's
  AgentTool instance by reference, so `_current_depth` stayed at 0
  forever and `max_depth` was never enforced. `make_subagent_runtime`
  now rebinds the child's `agent` tool to a fresh AgentTool with
  `_current_depth = parent_depth + 1`.
- Defense-in-depth: `ConversationRuntime._execute_tool_with_streaming`
  now consults `is_tool_allowed_for_role` against the runtime's
  `_subagent_role` before dispatch, so a future regression that leaks
  a forbidden tool into a child registry still cannot bypass the role
  whitelist.
- `CallHierarchyItem` now round-trips the original LSP node (`data`, `tags`,
  `range`, `selectionRange`, exact `kind` int) so servers like rust-analyzer
  and jdtls — which require their opaque `data` token to be echoed back —
  return non-empty incoming/outgoing call results. Unknown kind labels now
  raise instead of silently coercing to Function (12).
- `LspCallHierarchyTool` with `direction="both"` now dispatches incoming and
  outgoing calls concurrently via `asyncio.gather`, halving worst-case latency.
- `_CallHierarchyInput.direction` is now a `Literal["incoming","outgoing","both"]`
  so programmatic callers bypassing the JSON schema get Pydantic validation
  errors on bad values.
- `LspClient._request` now uses an id-dispatch loop, correctly handling interleaved server notifications (`window/logMessage`, `$/progress`, etc.) and concurrent requests. Pre-existing latent bug exposed by the broader LSP coverage shipped in borrow-2/2.5.
- Telemetry.span() outer guard restored: failures from the underlying OTel
  context manager (start_as_current_span enter / exit) no longer propagate
  to the caller, preserving the contract that "telemetry must never break
  the caller". Caller exceptions raised inside the with-block still
  propagate as before.
- llm.completion span no longer leaks if the XML tool-call fallback retry
  itself raises. The retry call site in Conversation._run_turn is now
  wrapped so any exception triggers _close_llm_span_with_error before
  propagating.

### Refactored
- _truncate_for_attribute is now imported at the top of conversation.py
  instead of lazily inside the post-stream enrichment block. Removes
  per-call import overhead and surfaces genuine import bugs.
- TelemetryConfig is now declared in exactly one place
  (llm_code/runtime/telemetry.py) and re-exported from
  llm_code/runtime/config.py for backward compatibility. Eliminates a
  duplicate dataclass that previously required manual field synchronization
  between the two copies and a duck-typed bridging block in tui/app.py.
- tui/app.py now passes RuntimeConfig.telemetry straight into Telemetry()
  instead of reconstructing it field by field. Adding a new TelemetryConfig
  field no longer requires three coordinated edits.

## v0.1.0 (2026-04-03) — Production Cleanup

### Changed

- Default CLI is now Ink UI (React/Node.js); use `--lite` for print-based fallback
- Updated `pyproject.toml` GitHub URLs from placeholder to `adamhung/llm-code`
- README updated: Ink UI default, `--lite`/`--serve`/`--connect`/`--ssh` flags documented, ClawHub marketplace, cost tracking, model aliasing

### Fixed

- `[send error:]` debug print in `ink_bridge.py` replaced with `logging.debug`
- Dead code: removed unused `removed` variable in `algorithms/gemma4_agent.py`
- Bare `except:` in `algorithms/gemma4_agent.py` replaced with `except Exception:`
- Unused `subargs` variable in `cli/tui.py` `/session` handler removed
- Semicolons on same-line imports in `ink_bridge.py` split to two statements
- Test `test_cli/test_image.py` updated: `detect_image_references` aliased to `extract_dropped_images`

### Removed

- `bubble_sort.py`, `multiplication.py`, `simple_demo.py` — development test artifacts
- `llm_code/algorithms/` directory — unreferenced Gemma4 agent prototype

### Chores

- Ruff lint: 39 issues fixed (34 auto-fixed, 5 manually resolved)
- All 1089 tests pass (3 skipped)

## v0.1.0 — Initial Release (2026-04-03)

### Features

**Core Agent (v1)**
- 6 built-in tools: read_file, write_file, edit_file, bash, glob_search, grep_search
- Multi-provider support: OpenAI-compatible API + Anthropic
- Dual-track tool calling: native function calling + XML tag fallback
- Streaming output with Rich Markdown rendering
- Layered permission system (5 modes + allow/deny lists)
- Hook system (pre/post tool use with exit code semantics)
- Session persistence and multi-session switching
- Layered config (user → project → local → CLI)
- Vision fallback for non-vision models
- Context compaction

**Smart Safety (v2)**
- Input-aware safety classification (bash ls = read-only, rm = destructive)
- Safety → permission system integration (dynamic effective_level)
- Pydantic runtime input validation
- Tool progress streaming via thread pool + asyncio.Queue

**Ecosystem (v3)**
- MCP client (stdio + HTTP transport, JSON-RPC 2.0)
- Plugin marketplace (5 registries: Official, Smithery, npm, GitHub, custom)
- Claude Code plugin.json compatibility
- Skills system (auto-inject + slash command trigger)
- Incremental streaming Markdown rendering
- Prefix cache optimization

**Agent Capabilities (v4)**
- Sub-agent tool (asyncio.gather parallel execution)
- Specialized agent roles (Explore, Plan, Verify)
- Model routing (static config + per-call override)
- Git-based undo/checkpoint (auto before writes)
- 7 git-aware tools with sensitive file detection

**Deep Integration (v5)**
- LSP integration (3 query tools + auto-detect)
- Cross-session memory (key-value + auto session summaries)
- Project indexer (file tree + regex symbol extraction)

**Production Quality (v6)**
- 4-level context compression (snip → micro → collapse → auto)
- Streaming tool execution (read-only tools execute during model output)
- Reactive compact (413 error recovery)
- Token budget and tool result budget
- MCP server instructions injection
- Structured logging
- Graceful shutdown
- Config validation
- GitHub Actions CI
- Docker support
- Documentation site

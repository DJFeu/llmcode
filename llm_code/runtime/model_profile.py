"""Declarative per-model profile system.

Replaces scattered hardcoded model adaptations (XML fallback, implicit
thinking, reasoning field names, pricing, etc.) with a single
``ModelProfile`` dataclass per model. Profiles are resolved by the
``ProfileRegistry`` which merges built-in defaults → built-in model
profiles → user TOML overrides.

Resolution order:
1. Exact model name match (e.g. "Qwen3.5-122B-A10B")
2. Prefix match (e.g. "qwen3.5" matches "qwen3.5:4b", "qwen3.5-7b")
3. Provider-family match (e.g. "claude-" → anthropic defaults)
4. Default profile

All fields have sensible defaults so a missing profile still works.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelProfile:
    """Declarative capability + behaviour profile for a single model.

    Every field has a safe default so callers never need to None-check.
    The profile is frozen to prevent accidental mutation during a turn.
    """

    # ── Identity ──────────────────────────────────────────────────────
    name: str = ""  # human-readable label (e.g. "Qwen3.5-122B")

    # ── Provider capabilities ─────────────────────────────────────────
    provider_type: str = "openai-compat"  # "openai-compat" | "anthropic"
    native_tools: bool = True
    supports_reasoning: bool = False
    supports_images: bool = False

    # ── Tool calling ──────────────────────────────────────────────────
    force_xml_tools: bool = False  # skip native tool attempt, go XML directly

    # ── Streaming / thinking ──────────────────────────────────────────
    implicit_thinking: bool = False  # vLLM injects <think> prefix
    reasoning_field: str = ""  # e.g. "reasoning_content", "" = auto-detect
    thinking_extra_body_format: str = "chat_template_kwargs"  # or "anthropic_native"
    default_thinking_budget: int = 10000
    # v2.8.1 — per-iteration thinking budget for the post-tool consumption
    # phase (iteration > 0 AND a tool was already called this turn). The
    # rationale: iteration 0 needs full thinking to decide which tool to
    # call and how to shape the args, but iteration 1+ is summarising a
    # tool result that's already ground truth — deep reasoning at that
    # phase is largely redundant and burns 30-90s of wall clock on slow
    # local models. ``0`` (the default) disables the override and the
    # full ``default_thinking_budget`` is used for every iteration,
    # preserving v2.8.0 behaviour byte-for-byte. GLM-5.1's profile opts
    # in to a 1024-token cap on consumption iterations.
    post_tool_thinking_budget: int = 0

    # ── Sampling ──────────────────────────────────────────────────────
    default_temperature: float = -1.0  # -1 = use config; >=0 = model-specific default
    reasoning_effort: str = ""  # "" = use default; "low" | "medium" | "high" | "max"
    is_small_model: bool = False  # nano/mini/flash/haiku — reduced thinking

    # ── Pricing (per 1M tokens) ───────────────────────────────────────
    price_input: float = 0.0  # 0 = unknown / free
    price_output: float = 0.0

    # ── Deployment ────────────────────────────────────────────────────
    is_local: bool = False  # self-hosted / private network — unlimited token upgrades
    unlimited_token_upgrade: bool = False  # explicit override for token upgrade cap

    # ── Routing ───────────────────────────────────────────────────────
    tier_c_model: str = ""  # model for SkillRouter Tier C; "" = use self

    # ── Limits ────────────────────────────────────────────────────────
    max_output_tokens: int = 4096
    context_window: int = 128000  # advertised context length

    # ── v13: profile-driven adapters ─────────────────────────────────
    # Replaced the hardcoded if-ladder that used to live in
    # ``runtime/prompt.py::select_intro_prompt`` (GA'd in v2.3.0).
    # Every built-in TOML now declares its own [prompt]/[parser]/
    # [parser_hints] sections; the shim is kept only as a
    # deprecation warning surface (removal scheduled for v14).
    #
    # TOML authoring (flat sections to match existing convention):
    #
    #     [prompt]
    #     template = "models/glm.j2"
    #     match = ["glm", "zhipu"]
    #
    #     [parser]
    #     variants = ["json_payload", "hermes_function", "harmony_kv",
    #                 "glm_brace", "bare_name_tag"]
    #
    #     [parser_hints]
    #     custom_close_tags = ["</arg_value>"]
    #     call_separator_chars = "→ \t\r\n"
    #
    # ``prompt_template`` — path under ``engine/prompts/`` (e.g.
    #   ``"models/glm.j2"``). Read by ``load_intro_prompt(profile)``.
    # ``prompt_match`` — lowercase substrings; first profile whose
    #   match list contains a substring of the user's model id is
    #   picked by ``resolve_profile_for_model(model_id)``.
    # ``parser_variants`` — ordered variant names consumed by
    #   ``tools/parser_variants.REGISTRY``. Empty = use
    #   ``DEFAULT_VARIANT_ORDER``.
    # ``custom_close_tags`` + ``call_separator_chars`` — feed
    #   ``view/stream_parser.StreamParser`` to replace the
    #   GLM-specific heuristic for variant 6 / variant 7 handling.
    prompt_template: str = ""
    prompt_match: tuple[str, ...] = ()
    parser_variants: tuple[str, ...] = ()
    custom_close_tags: tuple[str, ...] = ()
    call_separator_chars: str = ""

    # ── v14 — tool consumption compat layer ──────────────────────────
    # Three optional mechanisms that paper over a class of model-level
    # instruction-following weaknesses where a model calls a tool,
    # receives data, and then writes a `content` response that
    # contradicts the tool result (canonical "I don't have access to X
    # after just calling X" failure mode). Each mechanism is gated
    # independently so adopters opt in per profile.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [tool_consumption]
    #     reminder_after_each_call = true   # Mechanism A — default on
    #     strip_prior_reasoning = false     # Mechanism B — default off
    #     retry_on_denial = false           # Mechanism C — default off
    #
    # ``reminder_after_each_call`` — append a synthetic
    #   ``<system-reminder>`` user message immediately after each tool
    #   result, naming the tool just used. Cheapest of the three;
    #   ~40 tokens per tool call. Default ON so every model gets the
    #   protection unless the profile explicitly opts out.
    # ``strip_prior_reasoning`` — drop ``reasoning_content`` /
    #   ``reasoning`` keys from prior assistant messages on the
    #   outbound request. Trades multi-turn reasoning continuity for
    #   grounded single-turn responses. Recommended for separate-
    #   reasoning-channel models that bleed denials across turns
    #   (GLM-5.1, DeepSeek-R1).
    # ``retry_on_denial`` — after a turn's content streams, scan for
    #   denial keywords; if a tool was called this turn AND a denial
    #   pattern matches, re-invoke the provider once with an injected
    #   continuation reminder. Capped at 1 retry. Buffers streaming
    #   for retry-eligible turns (TTFT trade-off). Costs +1 provider
    #   call per denial-matched turn. Adopter-only opt-in.
    reminder_after_each_call: bool = True
    strip_prior_reasoning: bool = False
    retry_on_denial: bool = False

    # ── v15 — borrow from free-claude-code ───────────────────────────
    # Three optional capability flags ported from
    # ``Alishahryar1/free-claude-code`` (the Claude Code → any-LLM proxy
    # at ``/Users/adamhong/Work/qwen/reference/free-claude-code``):
    #
    # * **Request optimizations** — intercept five trivial-call patterns
    #   (quota probe, prefix detection, title generation, suggestion
    #   mode, filepath extraction) at the provider entry point and
    #   short-circuit with a synthetic response. Saves quota + latency
    #   on Claude-Code-style traffic where these patterns recur. On by
    #   default; profiles that want every call to hit the model (e.g.
    #   testing) opt out.
    # * **Proactive rate limiter** — sliding-window async limiter on
    #   the provider HTTP layer. Avoids 429 round-trips on free-tier
    #   endpoints with hard per-minute caps (e.g. NVIDIA NIM 40/min).
    #   Disabled when ``proactive_rate_limit_per_minute == 0``
    #   (default — preserves current behaviour for unconfigured
    #   profiles). Optional ``proactive_rate_limit_concurrency`` caps
    #   simultaneous in-flight calls independently of the per-minute
    #   gate.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [runtime]
    #     enable_request_optimizations = true
    #
    #     [provider]
    #     proactive_rate_limit_per_minute = 40
    #     proactive_rate_limit_concurrency = 4
    enable_request_optimizations: bool = True
    proactive_rate_limit_per_minute: int = 0
    proactive_rate_limit_concurrency: int = 0

    # ── v16 (v2.6.0) — audit closure + cross-project borrow ─────────
    # Four optional flags introduced in M1's commit so M2 / M4 / M10
    # don't double-bump the profile schema (see spec §4):
    #
    # * ``agent_memory_enabled`` (M2) — when True, ``subagent_factory``
    #   injects ``memory_read``/``memory_write``/``memory_list`` tools
    #   per spawn, scoped by ``agent_id``. Default on; profiles that
    #   want lean subagents flip it off.
    # * ``mcp_approval_granularity`` (M10) — ``"tool"`` (default,
    #   matches v2.5.x) approves a tool name once for the session;
    #   ``"call"`` requires re-approval per ``(tool, args_hash)`` pair.
    # * ``ui_theme`` (M4) — name of the active Rich theme; one of the
    #   8 built-in themes documented in
    #   ``llm_code.view.themes.BUILTIN_THEMES``. Falls back to
    #   ``"default"`` when unrecognised.
    # * ``vim_mode`` (M4) — runtime-toggleable via ``/vim``; persisted
    #   on the profile so the user's preference survives reload.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [runtime]
    #     agent_memory_enabled = true
    #
    #     [mcp]
    #     approval_granularity = "tool"   # or "call"
    #
    #     [ui]
    #     theme = "default"
    #     vim_mode = false
    agent_memory_enabled: bool = True
    mcp_approval_granularity: str = "tool"
    ui_theme: str = "default"
    vim_mode: bool = False

    # ── v2.6.1 M2 — prompt-dedupe with template ──────────────────────
    # When True, the prompt builder skips generic snippets whose
    # ``tags`` are fully covered by the active model template's
    # ``provides_tags`` (declared in ``<template>.metadata.toml``
    # alongside the ``.j2`` file).  Eliminates ~1500 chars of
    # duplicate guidance per turn for models like GLM-5.1 whose
    # custom template already expresses the rules. Default ``False``
    # preserves v2.6.0 byte-parity for every profile that doesn't
    # explicitly opt in.
    #
    # TOML authoring:
    #
    #     [prompt]
    #     dedupe_with_template = true
    prompt_dedupe_with_template: bool = False

    # ── v2.8.0 — RAG pipeline ────────────────────────────────────────
    # All seven flags introduced in v17 M1's commit so M2-M6 don't
    # double-bump the dataclass schema (see spec §4):
    #
    # * ``rerank_backend`` (M1) — selects the rerank implementation
    #   ``"local"`` (sentence-transformers cross-encoder, default),
    #   ``"cohere"``, ``"jina"``, or ``"none"`` (identity passthrough).
    # * ``research_query_expansion`` (M2) — controls multi-query
    #   expansion strategy for the ``research`` tool.
    #     ``"off"``      → single-shot, no expansion.
    #     ``"template"`` → pattern-rule expansion (free, default).
    #     ``"llm"``      → tier_c_model round-trip; falls back to
    #                      template on parse error.
    # * ``research_max_subqueries`` (M2) — cap on expanded sub-queries.
    # * ``research_default_depth`` (M5) — default depth for the
    #   ``research`` tool when the LLM omits it.
    #     ``"fast"``     → 1 sub-query, K=3, no rerank.
    #     ``"standard"`` → 3 sub-queries, K=5, rerank.
    #     ``"deep"``     → 3 sub-queries, K=10, rerank.
    # * ``research_max_concurrency`` (M5) — semaphore cap on in-flight
    #   HTTP during ``research`` pipeline gather() phases.
    # * ``linkup_default_mode`` (M3) — ``"searchResults"`` (default,
    #   matches v2.7.0 behaviour) or ``"sourcedAnswer"`` (Linkup
    #   returns a citation-grounded answer in one round-trip; the
    #   research tool short-circuits to it when healthy).
    # * ``backend_health_check_enabled`` (M4) — flip to False to
    #   disable circuit-breaker fallback ordering for deterministic
    #   tests / CI scenarios.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [research]
    #     query_expansion = "template"
    #     max_subqueries = 3
    #     default_depth = "standard"
    #     max_concurrency = 5
    #
    #     [rerank]
    #     backend = "local"
    #
    #     [linkup]
    #     default_mode = "searchResults"
    #
    #     [backend_health]
    #     check_enabled = true
    rerank_backend: str = "local"
    research_query_expansion: str = "template"
    research_max_subqueries: int = 3
    research_default_depth: str = "standard"
    research_max_concurrency: int = 5
    linkup_default_mode: str = "searchResults"
    backend_health_check_enabled: bool = True

    # ── v2.9.0 P1 — parallel tool dispatch ───────────────────────────
    # When the model emits multiple tool_calls in a single assistant
    # turn, dispatch them via ``asyncio.gather`` instead of the legacy
    # sequential ``for`` loop. Tool results return in the original
    # ``tool_call_id`` order so the model sees a deterministic shape
    # regardless of completion order. Default ``True`` — safe
    # everywhere because v2.8.1 was already running read-only tools
    # concurrently via ``StreamingToolExecutor``; this lever extends
    # the same model to write-pending and non-pre-computed calls.
    # Profiles can pin ``False`` as a safety valve when a server has
    # known concurrency issues.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [parallel_tools]
    #     enable_parallel_tools = true
    enable_parallel_tools: bool = True

    # ── v2.9.0 P2 — tool-result compression on re-feed ──────────────
    # When the conversation is serialized for iteration N+1, replace
    # older ``ToolResultBlock`` payloads with a 500-char preview +
    # structured marker. The most recent contiguous tool-result
    # batch stays full so the model still has complete data for the
    # current iteration's reasoning. Drops 60-80% of prefill tokens
    # on multi-search workflows where llama.cpp re-prefills the full
    # conversation history every iter (no prompt cache).
    #
    # Default ``False`` so cloud / Anthropic profiles keep v2.8.1
    # byte-parity (Anthropic prompt caching already amortises stable
    # prefixes); GLM-5.1 opts in via its profile.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [tool_consumption]
    #     compress_old_tool_results = true
    compress_old_tool_results: bool = False

    # ── v2.9.0 P3 — final compile thinking=0 ────────────────────────
    # When iter > 0 AND ``tool_calls_this_turn >= compile_after_tool_calls > 0``,
    # drop the thinking budget to ``compile_thinking_budget``
    # (typically 0). The "compile" step (final summarisation after N
    # tool results) doesn't need reasoning — it's templating work:
    # extract title from result[0], URL from result[1], format. Deep
    # chain-of-thought there reasons over ground truth and adds little
    # signal at large wall-clock cost on slow local models.
    #
    # Default ``compile_after_tool_calls = 0`` is the disable sentinel
    # so v2.8.1's per-iteration ``post_tool_thinking_budget`` stays in
    # effect for profiles that don't opt in. When the lever engages
    # AND ``compile_thinking_budget == 0``, the runtime emits a fully
    # disabled thinking config (no enable=True, no budget) for that
    # single call.
    #
    # TOML authoring (matches the existing flat-section convention):
    #
    #     [tool_consumption]
    #     compile_after_tool_calls = 3
    #     compile_thinking_budget = 0
    compile_after_tool_calls: int = 0
    compile_thinking_budget: int = 0


# ── Built-in profiles ─────────────────────────────────────────────────

_BUILTIN_PROFILES: dict[str, ModelProfile] = {
    # ── Qwen family ───────────────────────────────────────────────────
    "qwen3.5-122b": ModelProfile(
        name="Qwen3.5-122B-A10B",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        supports_images=False,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=16384,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=True,
        context_window=131072,
    ),
    "qwen3.5": ModelProfile(
        name="Qwen3.5",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        supports_images=False,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=10000,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=True,
        context_window=131072,
    ),
    "qwen3": ModelProfile(
        name="Qwen3",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        supports_images=False,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=8192,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=True,
        context_window=32768,
    ),

    # ── Qwen cloud flagships (ModelStudio / Dashscope OpenAI-compat) ──
    # Source: qwen-code README + packages/core/src/core/openaiContentGenerator/
    # qwen3.6-plus went live 2026-04-02; qwen3.5-plus went live 2026-02-16.
    "qwen3.6-plus": ModelProfile(
        name="Qwen3.6-Plus",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=True,
        supports_images=False,
        force_xml_tools=False,
        implicit_thinking=False,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=16384,
        default_temperature=0.55,
        reasoning_effort="high",
        is_local=False,
        context_window=262144,
        max_output_tokens=16384,
    ),
    "qwen3.5-plus": ModelProfile(
        name="Qwen3.5-Plus",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=True,
        supports_images=False,
        force_xml_tools=False,
        implicit_thinking=False,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=16384,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=False,
        context_window=262144,
        max_output_tokens=16384,
    ),
    "qwen3-max": ModelProfile(
        name="Qwen3-Max",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        supports_images=False,
        default_temperature=0.55,
        is_local=False,
        # dashscope.test.ts documents the 32K output clamp for qwen3-max
        max_output_tokens=32768,
        context_window=262144,
    ),
    "qwen3-vl-plus": ModelProfile(
        name="Qwen3-VL-Plus",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        supports_images=True,
        default_temperature=0.55,
        is_local=False,
        context_window=131072,
        max_output_tokens=8192,
    ),

    # ── Qwen3-Coder family ───────────────────────────────────────────
    # qwen-code packages/core/src/core/tokenLimits.ts records:
    #   qwen3-coder-plus / -flash / -plus-20250601  → 1,000,000
    #   qwen3-coder-7b  / qwen3-coder-next          →   262,144
    # Cloud "-plus" / "-flash" variants run through the Dashscope
    # OpenAI-compat endpoint (native tools). Bare "qwen3-coder" +
    # sized variants (30ba3b, 480a35, 7b) are the open-source weights
    # users self-host on vLLM/Ollama/LM Studio — force XML + implicit
    # thinking, matching the existing qwen3.5-122b pattern.
    "qwen3-coder-plus": ModelProfile(
        name="Qwen3-Coder-Plus",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        force_xml_tools=False,
        default_temperature=0.55,
        is_local=False,
        context_window=1_000_000,
        max_output_tokens=16384,
    ),
    "qwen3-coder-flash": ModelProfile(
        name="Qwen3-Coder-Flash",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        force_xml_tools=False,
        default_temperature=0.55,
        is_local=False,
        is_small_model=True,
        context_window=1_000_000,
        max_output_tokens=8192,
    ),
    "qwen3-coder-7b": ModelProfile(
        name="Qwen3-Coder-7B",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=8192,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=True,
        is_small_model=True,
        context_window=262144,
        max_output_tokens=8192,
    ),
    "qwen3-coder": ModelProfile(
        # Generic fallback for self-hosted OSS variants: qwen3-coder-30ba3b,
        # qwen3-coder-480a35, qwen3-coder-next, and any future sized drops.
        name="Qwen3-Coder (OSS)",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_thinking_budget=12288,
        default_temperature=0.55,
        reasoning_effort="medium",
        is_local=True,
        context_window=262144,
        max_output_tokens=8192,
    ),

    # ── Anthropic family ──────────────────────────────────────────────
    "claude-opus-4-6": ModelProfile(
        name="Claude Opus 4.6",
        provider_type="anthropic",
        native_tools=True,
        supports_reasoning=True,
        supports_images=True,
        thinking_extra_body_format="anthropic_native",
        default_thinking_budget=10000,
        reasoning_effort="high",
        price_input=15.00,
        price_output=75.00,
        max_output_tokens=16384,
        context_window=200000,
    ),
    "claude-sonnet-4-6": ModelProfile(
        name="Claude Sonnet 4.6",
        provider_type="anthropic",
        native_tools=True,
        supports_reasoning=True,
        supports_images=True,
        thinking_extra_body_format="anthropic_native",
        default_thinking_budget=10000,
        reasoning_effort="medium",
        price_input=3.00,
        price_output=15.00,
        max_output_tokens=16384,
        context_window=200000,
    ),
    "claude-haiku-4-5": ModelProfile(
        name="Claude Haiku 4.5",
        provider_type="anthropic",
        native_tools=True,
        supports_reasoning=True,
        supports_images=True,
        thinking_extra_body_format="anthropic_native",
        default_thinking_budget=8000,
        reasoning_effort="low",
        is_small_model=True,
        price_input=0.80,
        price_output=4.00,
        max_output_tokens=8192,
        context_window=200000,
    ),

    # ── DeepSeek family ───────────────────────────────────────────────
    "deepseek-r1": ModelProfile(
        name="DeepSeek-R1",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_temperature=0.6,
        reasoning_effort="high",
        price_input=0.55,
        price_output=2.19,
        context_window=128000,
    ),
    "deepseek-chat": ModelProfile(
        name="DeepSeek Chat",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        price_input=0.27,
        price_output=1.10,
        context_window=128000,
    ),

    # ── OpenAI family ─────────────────────────────────────────────────
    "gpt-4o": ModelProfile(
        name="GPT-4o",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        supports_images=True,
        price_input=2.50,
        price_output=10.00,
        max_output_tokens=16384,
        context_window=128000,
    ),
    "gpt-4o-mini": ModelProfile(
        name="GPT-4o Mini",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=False,
        supports_images=True,
        is_small_model=True,
        price_input=0.15,
        price_output=0.60,
        max_output_tokens=16384,
        context_window=128000,
    ),
    "o3": ModelProfile(
        name="OpenAI o3",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=True,
        reasoning_field="reasoning",
        default_temperature=1.0,
        reasoning_effort="high",
        price_input=2.00,
        price_output=8.00,
        context_window=200000,
    ),
    "o4-mini": ModelProfile(
        name="OpenAI o4-mini",
        provider_type="openai-compat",
        native_tools=True,
        supports_reasoning=True,
        reasoning_field="reasoning",
        default_temperature=1.0,
        reasoning_effort="medium",
        price_input=0.50,
        price_output=2.00,
        context_window=200000,
    ),
}

# Provider-family defaults (matched by prefix when no exact/prefix match)
_FAMILY_DEFAULTS: dict[str, ModelProfile] = {
    "claude-": ModelProfile(
        name="Claude (default)",
        provider_type="anthropic",
        native_tools=True,
        supports_reasoning=True,
        supports_images=True,
        thinking_extra_body_format="anthropic_native",
        price_input=3.00,
        price_output=15.00,
        context_window=200000,
    ),
    "gpt-": ModelProfile(
        name="GPT (default)",
        provider_type="openai-compat",
        native_tools=True,
        supports_images=True,
        price_input=2.50,
        price_output=10.00,
        context_window=128000,
    ),
    "deepseek-": ModelProfile(
        name="DeepSeek (default)",
        provider_type="openai-compat",
        native_tools=True,
        reasoning_field="reasoning_content",
        price_input=0.27,
        price_output=1.10,
        context_window=128000,
    ),
    "qwen": ModelProfile(
        name="Qwen (default)",
        provider_type="openai-compat",
        native_tools=False,
        supports_reasoning=True,
        force_xml_tools=True,
        implicit_thinking=True,
        reasoning_field="reasoning_content",
        thinking_extra_body_format="chat_template_kwargs",
        default_temperature=0.55,
        is_local=True,
        context_window=131072,
    ),
}

_DEFAULT_PROFILE = ModelProfile(name="(default)")


def _detect_small_model(model_name: str) -> bool:
    """Detect if a model is a small/lightweight variant by name patterns."""
    name_lower = model_name.lower()
    small_patterns = ("mini", "nano", "flash", "haiku", "small", "lite", "tiny")
    return any(p in name_lower for p in small_patterns)


# ── TOML loading ──────────────────────────────────────────────────────


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return the parsed dict.

    Uses the stdlib ``tomllib`` on Python 3.11+ and falls back to the
    PyPI ``tomli`` package (declared in ``pyproject.toml`` as a
    conditional dependency on older interpreters). Both have the same
    ``load`` API.
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover - exercised on 3.9/3.10 only
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


def _profile_from_dict(data: dict[str, Any], base: ModelProfile | None = None) -> ModelProfile:
    """Build a ModelProfile from a flat dict, optionally merging over a base.

    Unknown keys are silently ignored so user TOML files don't break
    when a field is removed in a future version.
    """
    if base is None:
        base = _DEFAULT_PROFILE

    valid_fields = {f.name for f in fields(ModelProfile)}
    # Flatten nested sections (e.g. [provider] type → provider_type)
    flat: dict[str, Any] = {}
    # Section map: TOML section name → either a single flat field name
    # (string) or a tuple of flat field names. The ``[provider]`` entry
    # is special — its bare ``type`` key maps to ``provider_type`` for
    # backward compatibility, and additional v15 keys (proactive rate
    # limit) are recognised in the same section.
    section_map = {
        "provider": "provider_type",
        "streaming": ("implicit_thinking", "reasoning_field"),
        "thinking": (
            "thinking_extra_body_format",
            "default_thinking_budget",
            # v2.8.1 — per-iteration budget for post-tool consumption.
            "post_tool_thinking_budget",
        ),
        "pricing": ("price_input", "price_output"),
        "limits": ("max_output_tokens", "context_window"),
        "sampling": ("default_temperature", "reasoning_effort"),
        "deployment": ("is_local", "unlimited_token_upgrade", "is_small_model"),
        "routing": ("tier_c_model",),
        # v13 Phase A sections; v2.6.1 M2 extends with ``dedupe_with_template``.
        "prompt": (
            "prompt_template",
            "prompt_match",
            "prompt_dedupe_with_template",
        ),
        "parser": ("parser_variants",),
        "parser_hints": ("custom_close_tags", "call_separator_chars"),
        # v14 — tool consumption compat layer; v2.9.0 P2 adds
        # compress_old_tool_results to the same section since both
        # mechanisms shape how tool results re-feed; v2.9.0 P3 adds
        # the compile-thinking heuristic fields here too because the
        # heuristic gates on tool_calls_this_turn — same problem
        # surface (post-tool consumption shape).
        "tool_consumption": (
            "reminder_after_each_call",
            "strip_prior_reasoning",
            "retry_on_denial",
            "compress_old_tool_results",
            "compile_after_tool_calls",
            "compile_thinking_budget",
        ),
        # v2.9.0 P1 — parallel tool dispatch lever lives in its own
        # section so opting in / out doesn't require touching the
        # tool_consumption block.
        "parallel_tools": ("enable_parallel_tools",),
        # v15 — borrow from free-claude-code; v16 extends with agent_memory.
        "runtime": (
            "enable_request_optimizations",
            "agent_memory_enabled",
        ),
        # v16 M10 — fine-grained MCP approval; section is ``[mcp]`` so it
        # never collides with the ``[provider]`` section that already
        # carries provider-specific knobs. The TOML key on disk is
        # ``approval_granularity`` to match the spec; the flat field is
        # ``mcp_approval_granularity`` to keep the dataclass tidy.
        "mcp": ("mcp_approval_granularity",),
        # v16 M4 — UI surfaces (theme + vim mode) on a dedicated section.
        "ui": ("ui_theme", "vim_mode"),
        # v2.8.0 — RAG pipeline. Seven flags spread across four
        # sections so each mechanism owns its own TOML namespace
        # without colliding with existing sections.
        "rerank": ("rerank_backend",),
        "research": (
            "research_query_expansion",
            "research_max_subqueries",
            "research_default_depth",
            "research_max_concurrency",
        ),
        "linkup": ("linkup_default_mode",),
        "backend_health": ("backend_health_check_enabled",),
    }
    for key, value in data.items():
        if isinstance(value, dict):
            # Nested TOML section
            mapping = section_map.get(key)
            if mapping is None:
                # Unknown section — try to flatten keys directly
                for subkey, subval in value.items():
                    if subkey in valid_fields:
                        flat[subkey] = subval
            elif isinstance(mapping, str):
                # Single-field section (e.g. [provider] type → provider_type).
                # The ``[provider]`` section also accepts named keys
                # (e.g. ``proactive_rate_limit_per_minute``) which map
                # directly onto flat fields.
                if "type" in value:
                    flat[mapping] = value["type"]
                for subkey, subval in value.items():
                    full_key = f"{key}_{subkey}" if f"{key}_{subkey}" in valid_fields else subkey
                    if full_key in valid_fields:
                        flat[full_key] = subval
            else:
                # Multi-field section
                for subkey, subval in value.items():
                    full_key = f"{key}_{subkey}" if f"{key}_{subkey}" in valid_fields else subkey
                    if full_key in valid_fields:
                        flat[full_key] = subval
        elif key in valid_fields:
            flat[key] = value

    # v13 Phase A: fields typed ``tuple[str, ...]`` must be coerced from
    # the ``list`` TOML parsers hand back. Do this in a single pass so
    # new tuple fields (parser_variants, custom_close_tags, prompt_match)
    # don't need to be hard-wired.
    _TUPLE_FIELDS = {
        f.name
        for f in fields(ModelProfile)
        if str(f.type).startswith("tuple[") or str(f.type).startswith("Tuple[")
    }

    # Merge: base fields + overrides from flat
    merged = {}
    for f in fields(ModelProfile):
        if f.name in flat:
            value = flat[f.name]
            if f.name in _TUPLE_FIELDS and isinstance(value, list):
                value = tuple(value)
            merged[f.name] = value
        else:
            merged[f.name] = getattr(base, f.name)

    return ModelProfile(**merged)


# ── Registry ──────────────────────────────────────────────────────────


class ProfileRegistry:
    """Resolves a model name to its ``ModelProfile``.

    Resolution order:
    1. User overrides (loaded from ``~/.llmcode/model_profiles/*.toml``)
    2. Built-in exact match (case-insensitive)
    3. Built-in prefix match (longest prefix wins)
    4. Family defaults (matched by model name prefix)
    5. Default profile
    """

    def __init__(
        self,
        user_profile_dir: Path | None = None,
        extra_profiles: dict[str, ModelProfile] | None = None,
    ) -> None:
        # Merge built-ins + extras
        self._profiles: dict[str, ModelProfile] = dict(_BUILTIN_PROFILES)
        if extra_profiles:
            self._profiles.update(extra_profiles)

        # Load user TOML overrides
        if user_profile_dir is None:
            user_profile_dir = Path.home() / ".llmcode" / "model_profiles"
        self._user_dir = user_profile_dir
        self._user_mtime: float = 0.0  # track directory mtime for hot-reload
        self._load_user_profiles()

    def _load_user_profiles(self) -> None:
        """Load *.toml files from the user profile directory."""
        if not self._user_dir.is_dir():
            return
        # Record dir mtime for hot-reload detection
        try:
            self._user_mtime = self._user_dir.stat().st_mtime
        except OSError:
            pass
        for toml_path in sorted(self._user_dir.glob("*.toml")):
            try:
                data = _load_toml(toml_path)
                key = toml_path.stem.lower()
                base = self._profiles.get(key, _DEFAULT_PROFILE)
                profile = _profile_from_dict(data, base=base)
                self._profiles[key] = profile
                _logger.debug("loaded user profile: %s from %s", key, toml_path)
            except Exception as exc:
                _logger.warning("failed to load profile %s: %s", toml_path, exc)

    def reload_if_changed(self) -> bool:
        """Re-read user TOML profiles if the directory was modified.

        Returns True if profiles were reloaded. Cheap to call frequently
        — only stats the directory, not individual files.
        """
        if not self._user_dir.is_dir():
            return False
        try:
            current_mtime = self._user_dir.stat().st_mtime
        except OSError:
            return False
        if current_mtime <= self._user_mtime:
            return False
        # Directory was touched — reload all user profiles
        _logger.info("model_profiles directory changed, reloading")
        # Reset to built-ins before reloading
        self._profiles = dict(_BUILTIN_PROFILES)
        self._load_user_profiles()
        return True

    def resolve(self, model: str) -> ModelProfile:
        """Resolve a model name to its profile."""
        key = model.lower()

        # 1. Exact match
        if key in self._profiles:
            result = self._profiles[key]
        else:
            # 2. Prefix match (longest prefix wins)
            best_match: str = ""
            for profile_key in self._profiles:
                if key.startswith(profile_key) and len(profile_key) > len(best_match):
                    best_match = profile_key
            if best_match:
                result = self._profiles[best_match]
            else:
                # 3. Family defaults
                result = _DEFAULT_PROFILE
                for family_prefix, family_profile in _FAMILY_DEFAULTS.items():
                    if key.startswith(family_prefix):
                        result = family_profile
                        break

        # Auto-detect small model by name if not already flagged
        if not result.is_small_model and _detect_small_model(key):
            from dataclasses import replace
            result = replace(result, is_small_model=True)

        return result

    def list_profiles(self) -> dict[str, ModelProfile]:
        """Return all registered profiles (built-in + user)."""
        return dict(self._profiles)


# ── Module-level singleton ────────────────────────────────────────────

_registry: ProfileRegistry | None = None


def get_profile(model: str) -> ModelProfile:
    """Resolve a model name to its profile using the global registry.

    The registry is lazily initialized on first call. Hot-reloads
    user TOML overrides if the profile directory was modified.
    """
    global _registry
    if _registry is None:
        _registry = ProfileRegistry()
    else:
        _registry.reload_if_changed()
    return _registry.resolve(model)


def get_registry() -> ProfileRegistry:
    """Return the global ProfileRegistry, initializing if needed."""
    global _registry
    if _registry is None:
        _registry = ProfileRegistry()
    return _registry


def probe_provider_profile(base_url: str, current_model: str) -> ModelProfile | None:
    """Probe a provider's ``/v1/models`` endpoint and resolve the best profile.

    Returns a ``ModelProfile`` if the probe finds a model ID that resolves
    to a more specific profile than the default, or ``None`` if the probe
    fails or the current profile is already the best match.

    This is a blocking HTTP call — call from a thread or during init.
    """
    import httpx

    try:
        url = f"{base_url.rstrip('/v1').rstrip('/')}/v1/models"
        resp = httpx.get(url, timeout=3.0)
        if resp.status_code != 200:
            return None

        data = resp.json().get("data", [])
        if not data:
            return None

        # Try each model ID from the provider
        registry = get_registry()
        current_profile = registry.resolve(current_model)
        for m in data:
            model_id = m.get("id", "")
            if not model_id:
                continue
            candidate = registry.resolve(model_id)
            # A named profile (not the default) is better than the default
            if candidate.name and candidate.name != "(default)" and (
                current_profile.name == "(default)" or current_profile.name == ""
            ):
                # Merge context window from probe if available
                mml = m.get("max_model_len", 0)
                if mml > 0 and mml != candidate.context_window:
                    from dataclasses import replace
                    candidate = replace(candidate, context_window=mml)
                _logger.info(
                    "auto-discovered profile '%s' for model '%s' from %s",
                    candidate.name, model_id, url,
                )
                return candidate

        return None
    except Exception as exc:
        _logger.debug("profile probe failed for %s: %s", base_url, exc)
        return None

"""Runtime configuration dataclasses and loader."""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from llm_code.harness.config import HarnessConfig, HarnessControl

# TelemetryConfig is owned by the telemetry subsystem; re-export here so the
# legacy import path `from llm_code.runtime.config import TelemetryConfig`
# continues to work for downstream callers.
from llm_code.runtime.telemetry import TelemetryConfig as TelemetryConfig

# Re-exports for backward compatibility (classes moved to submodules)
from llm_code.runtime.config_features import (  # noqa: E402
    DreamConfig as DreamConfig,
    VoiceConfig as VoiceConfig,
    ComputerUseConfig as ComputerUseConfig,
    IDEConfig as IDEConfig,
    SwarmConfig as SwarmConfig,
    WorktreeConfig as WorktreeConfig,
    VCRConfig as VCRConfig,
    HidaConfig as HidaConfig,
    DiminishingReturnsConfig as DiminishingReturnsConfig,
    KnowledgeConfig as KnowledgeConfig,
)
from llm_code.runtime.config_enterprise import (  # noqa: E402
    EnterpriseAuthConfig as EnterpriseAuthConfig,
    EnterpriseRBACConfig as EnterpriseRBACConfig,
    EnterpriseAuditConfig as EnterpriseAuditConfig,
    EnterpriseConfig as EnterpriseConfig,
)


@dataclass(frozen=True)
class MCPConfig:
    """Scoped MCP server configs.

    - ``always_on``: servers started at session init (legacy behavior).
    - ``on_demand``: servers spawned lazily by personas/skills via the
      user-approval flow. NOT started at session init.

    Backward compat: if a user's ``~/.llmcode/mcp.json`` (or the
    ``mcpServers`` block in the main config) is a flat dict with no
    ``always_on``/``on_demand`` keys, every entry is treated as
    ``always_on`` to match pre-lazy-MCP behavior.
    """

    always_on: dict = field(default_factory=dict)
    on_demand: dict = field(default_factory=dict)


def _parse_mcp_config(raw: dict) -> MCPConfig:
    """Parse either the new split schema or a legacy flat dict.

    New schema::

        {"always_on": {...}, "on_demand": {...}}

    Legacy schema::

        {"filesystem": {...}, "tavily": {...}}   # all treated as always_on

    Mixed / stranded schema (v2.5.0–v2.5.3 ``/mcp install`` bug
    history): the user's config has ``always_on`` (and/or
    ``on_demand``) AND top-level sibling entries left over from
    install commands that didn't know about the split schema. Those
    siblings would silently disappear under the old strict-split
    branch. v2.5.5 promotes them into ``always_on`` so users on any
    schema flavour written by any prior version recover their MCP
    servers without manual config editing.
    """
    if not isinstance(raw, dict) or not raw:
        return MCPConfig()
    if "always_on" in raw or "on_demand" in raw:
        always_raw = raw.get("always_on") or {}
        on_demand_raw = raw.get("on_demand") or {}
        if not isinstance(always_raw, dict):
            always_raw = {}
        if not isinstance(on_demand_raw, dict):
            on_demand_raw = {}
        # Promote stranded top-level entries (anything that isn't
        # ``always_on`` / ``on_demand`` and looks like a server config
        # dict) into ``always_on``. Pre-existing ``always_on`` entries
        # win on key collision so an explicit always_on declaration
        # overrides a stale stranded sibling.
        always = {}
        for key, value in raw.items():
            if key in {"always_on", "on_demand"}:
                continue
            if isinstance(value, dict):
                always[key] = value
        always.update(always_raw)
        return MCPConfig(always_on=always, on_demand=dict(on_demand_raw))
    return MCPConfig(always_on=dict(raw), on_demand={})


@dataclass(frozen=True)
class HookConfig:
    event: str          # "pre_tool_use" | "post_tool_use" | "on_stop" | glob pattern
    command: str
    tool_pattern: str = "*"
    timeout: float = 10.0
    on_error: str = "warn"  # "warn" | "deny" | "ignore"


@dataclass(frozen=True)
class VisionConfig:
    fallback: str = ""
    vision_model: str = ""
    vision_api: str = ""
    vision_api_key_env: str = ""


@dataclass(frozen=True)
class ModelRoutingConfig:
    sub_agent: str = ""
    compaction: str = ""
    # Legacy single-shot fallback. Kept for backward compatibility —
    # promoted to a 1-element FallbackChain when `fallbacks` is empty.
    fallback: str = ""
    # Wave2-3: declarative multi-step fallback chain. Evaluated in
    # order; empty tuple means "no chain, use legacy `fallback` if set".
    fallbacks: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompactionThresholdsConfig:
    trigger_pct: float = 0.85
    min_messages: int = 30
    min_text_blocks: int = 10
    target_pct: float = 0.50
    # C4: circuit breaker + output reserve. Mirrors
    # ``CompactionThresholds`` in ``runtime/auto_compact.py`` so users can
    # tune the breaker via their TOML config.
    max_consecutive_failures: int = 3
    output_token_reserve: int = 20_000


@dataclass(frozen=True)
class CompactionConfig:
    """Auto-compaction settings consumed by the conversation turn loop."""
    auto_enabled: bool = False
    thresholds: CompactionThresholdsConfig = field(
        default_factory=CompactionThresholdsConfig
    )


@dataclass(frozen=True)
class ThinkingConfig:
    mode: str = "adaptive"        # "adaptive" | "enabled" | "disabled"
    budget_tokens: int = 10000


@dataclass(frozen=True)
class WebFetchConfig:
    default_renderer: str = "default"
    browser_timeout: float = 30.0
    cache_ttl: float = 900.0
    cache_max_entries: int = 50
    max_length: int = 50_000


@dataclass(frozen=True)
class WebSearchConfig:
    default_backend: str = "duckduckgo"
    brave_api_key_env: str = "BRAVE_API_KEY"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    serper_api_key_env: str = "SERPER_API_KEY"
    # v2.7.0a1 M1 — Exa semantic / neural search (free 1000/mo).
    exa_api_key_env: str = "EXA_API_KEY"
    searxng_base_url: str = ""
    max_results: int = 10
    domain_allowlist: tuple[str, ...] = ()
    domain_denylist: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryConfig:
    """Memory subsystem configuration.

    Historical fields (kept for backward compatibility):
        strict_derivable_check: existing flag — untouched by v12 M7.

    v12 M7 fields (plan #7 Task 7.8):
        enabled: master switch — when False the five memory Components
            are not wired into the default pipeline.
        layer: storage backend name — ``hida`` / ``sqlite`` / ``in_memory``.
        hida_index_path: path to the HIDA index file (when layer=hida).
        embedder: embedding backend name — ``sentence_transformers`` (default),
            ``openai``, ``anthropic``, ``onnx``, or ``deterministic``.
        embedder_model: model identifier passed to the backend.
        reranker: reranker backend — ``cross_encoder_onnx`` (default),
            ``llm``, or ``noop``.
        reranker_model: model identifier for the reranker backend.
        retrieve_top_k: number of candidates pulled from the backend.
        rerank_top_k: number of entries kept after reranking.
        max_context_chars: soft cap on rendered memory-context text.
        default_scope: scope used when the caller doesn't pass one.
        remember_filter: policy — ``always`` / ``on_error_only`` /
            ``non_read_only_only`` / ``never``.
        context_template: ``default`` or ``compact``.
    """

    strict_derivable_check: bool = False
    # v12 M7 — Pipeline-borne memory
    enabled: bool = True
    layer: str = "in_memory"
    hida_index_path: str = ""
    embedder: str = "sentence_transformers"
    embedder_model: str = "all-MiniLM-L6-v2"
    reranker: str = "cross_encoder_onnx"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    retrieve_top_k: int = 20
    rerank_top_k: int = 5
    max_context_chars: int = 4000
    default_scope: str = "project"
    remember_filter: str = "always"
    context_template: str = "default"


@dataclass(frozen=True)
class CompressorConfig:
    llm_summarize: bool = False
    summarize_model: str = ""
    max_summary_tokens: int = 1000


# ---------------------------------------------------------------------------
# v12 engine configs
#
# Root of the v12 Haystack-borrow overhaul (spec:
# docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md).
#
# Post-M8.b: all runs flow through the engine path. The transitional flag
# that gated the parity suite during M1–M7 is gone; no user-facing feature
# flag survives the v2.0 cutover.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentLoopConfig:
    """Agent loop control policies (plan #3, M3)."""

    max_agent_steps: int = 50
    retry_policy: str = "no_retry"  # no_retry | exponential | rate_limit
    retry_max_attempts: int = 3
    fallback_policy: str = "none"  # none | semantic | model
    degraded_policy: str = "none"  # none | consecutive_failure | budget
    degraded_threshold: int = 3
    exit_conditions: tuple[str, ...] = ("max_steps",)
    retry_budget: int = 20


@dataclass(frozen=True)
class ObservabilityConfig:
    """OpenTelemetry + Langfuse + redaction + metrics (plan #6, M6)."""

    enabled: bool = True
    exporter: str = "console"  # otlp | langfuse | console | off
    otlp_endpoint: str = ""
    otlp_protocol: str = "http/protobuf"  # http/protobuf | grpc
    otlp_headers: tuple[tuple[str, str], ...] = ()
    langfuse_public_key_env: str = "LANGFUSE_PUBLIC_KEY"
    langfuse_secret_key_env: str = "LANGFUSE_SECRET_KEY"
    langfuse_host: str = "https://cloud.langfuse.com"
    service_name: str = "llmcode"
    service_version: str = ""
    resource_attrs: tuple[tuple[str, str], ...] = ()
    sample_rate: float = 1.0
    redact_log_records: bool = True
    redact_span_attributes: bool = True
    metrics_enabled: bool = True
    metrics_port: int = 0  # 0 = piggyback on hayhooks port


@dataclass(frozen=True)
class HayhooksConfig:
    """Headless transports (plan #4, M4)."""

    enabled: bool = False
    auth_token_env: str = "LLMCODE_HAYHOOKS_TOKEN"
    allowed_tools: tuple[str, ...] = ()
    max_agent_steps: int = 20
    request_timeout_s: float = 300.0
    rate_limit_rpm: int = 60
    enable_openai_compat: bool = True
    enable_mcp: bool = True
    enable_ide_rpc: bool = True  # absorbed from ide/server.py in M4.11
    enable_debug_repl: bool = False  # absorbed from remote/server.py in M4.11
    cors_origins: tuple[str, ...] = ()
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass(frozen=True)
class EngineConfig:
    """Root container for v12 engine subsystem configs.

    Assembled by `load_config`. All runs go through the engine path; the
    transitional flag that gated the parity suite during M1–M7 was deleted
    in the v2.0 cutover along with the legacy fallback.
    """

    agent_loop: AgentLoopConfig = field(default_factory=AgentLoopConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    hayhooks: HayhooksConfig = field(default_factory=HayhooksConfig)
    pipeline_stages: tuple[str, ...] = (
        "perm",
        "denial",
        "rate",
        "speculative",
        "resolver",
        "exec",
        "post",
    )


@dataclass(frozen=True)
class BashRule:
    pattern: str = ""
    action: str = "confirm"  # "allow" | "confirm" | "block"
    description: str = ""


@dataclass(frozen=True)
class BashRulesConfig:
    rules: tuple[BashRule, ...] = ()


def _default_sandbox_config():
    from llm_code.tools.sandbox import SandboxConfig
    return SandboxConfig()


@dataclass(frozen=True)
class TuiConfig:
    """TUI-specific settings (spinner verbs, etc.)."""

    spinner_verbs: tuple[str, ...] = ()
    spinner_verbs_mode: str = "append"  # "append" | "replace"


@dataclass(frozen=True)
class ThemeConfig:
    """User-configurable theme overrides for the v2.0.0 REPL palette.

    M15 introduces a semantic color map (``llm_code.view.repl.style``)
    with a tech-blue default tone. Any slot a user sets here replaces
    the M15 default in :class:`BrandPalette`. Keys correspond to the
    attribute names on ``BrandPalette`` (e.g. ``assistant_bullet``,
    ``diff_add_fg``, ``mode_bash_fg``).

    All values are plain Rich-compatible color strings — either a
    named color ("green", "bright_white"), a hex value ("#1e7ce8"),
    or a compound style ("bold cyan", "LLMCODE_BLUE_MID underline").
    Unknown keys are silently ignored so a user config written for a
    newer llmcode remains forward-compatible with an older build.
    """

    overrides: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SkillRouterConfig:
    """Configuration for the 3-tier skill router."""

    enabled: bool = True
    tier_a: bool = True            # keyword matching
    tier_b: bool = True            # TF-IDF similarity
    tier_c: bool = False           # LLM classifier (adds latency)
    tier_c_auto_for_cjk: bool = True  # auto-enable Tier C when prompt has CJK and Tier A/B miss
    similarity_threshold: float = 0.3
    max_skills_per_turn: int = 2
    tier_c_model: str = ""         # empty = use same model
    tier_c_timeout: float = 15.0   # max seconds for Tier C LLM classifier before skip


@dataclass(frozen=True)
class BuiltinHooksConfig:
    """Opt-in registration of in-process Python builtin hooks."""

    enabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordsConfig:
    """Keyword-driven action detection (Feature 6)."""

    enabled: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    config_version: str = ""
    model: str = ""
    provider_base_url: str | None = None
    provider_api_key_env: str = "LLM_API_KEY"
    permission_mode: str = "prompt"
    max_turn_iterations: int = 5
    max_tokens: int = 4096
    temperature: float = 0.7
    hooks: tuple[HookConfig, ...] = ()
    allowed_tools: frozenset[str] = frozenset()
    denied_tools: frozenset[str] = frozenset()
    compact_after_tokens: int = 80000
    timeout: float = 120.0
    max_retries: int = 2
    native_tools: bool = True
    vision: VisionConfig = field(default_factory=VisionConfig)
    model_routing: ModelRoutingConfig = field(default_factory=ModelRoutingConfig)
    mcp_servers: dict = field(default_factory=dict)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    registries: dict = field(default_factory=dict)
    skills_dirs: tuple[str, ...] = ()
    lsp_servers: dict = field(default_factory=dict)
    lsp_auto_detect: bool = True
    model_aliases: dict = field(default_factory=dict)
    pricing: dict = field(default_factory=dict)
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    vim_mode: bool = False
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)
    computer_use: ComputerUseConfig = field(default_factory=ComputerUseConfig)
    ide: IDEConfig = field(default_factory=IDEConfig)
    swarm: SwarmConfig = field(default_factory=SwarmConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    vcr: VCRConfig = field(default_factory=VCRConfig)
    hida: HidaConfig = field(default_factory=HidaConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    web_fetch: WebFetchConfig = field(default_factory=WebFetchConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    max_budget_usd: float | None = None
    compressor: CompressorConfig = field(default_factory=CompressorConfig)
    bash_rules: BashRulesConfig = field(default_factory=BashRulesConfig)
    sandbox: object = field(default_factory=lambda: _default_sandbox_config())
    enterprise: EnterpriseConfig = field(default_factory=EnterpriseConfig)
    output_compression: bool = True
    auto_commit: bool = False
    lsp_auto_diagnose: bool = True
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    skill_router: SkillRouterConfig = field(default_factory=lambda: SkillRouterConfig())
    diminishing_returns: DiminishingReturnsConfig = field(default_factory=lambda: DiminishingReturnsConfig())
    tui: TuiConfig = field(default_factory=TuiConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    builtin_hooks: BuiltinHooksConfig = field(default_factory=BuiltinHooksConfig)
    keywords: KeywordsConfig = field(default_factory=KeywordsConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)


class ConfigSchema(BaseModel):
    """Pydantic schema for validating the merged config dict before conversion."""

    model: str = ""
    provider: dict = {}
    permissions: dict = {}
    model_routing: dict = {}
    vision: dict = {}
    hooks: list = []
    mcpServers: dict = {}
    lspServers: dict = {}
    registries: dict = {}
    lsp_auto_detect: bool = True
    max_turn_iterations: int = 5
    thinking: dict = {}
    max_tokens: int = 4096
    temperature: float = 0.7
    compact_after_tokens: int = 80000
    native_tools: bool = True
    compressor: dict = {}
    bash_rules: list = []

    @field_validator("temperature")
    @classmethod
    def temp_range(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v

    @field_validator("max_tokens")
    @classmethod
    def tokens_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_tokens must be positive")
        return v

    @field_validator("max_turn_iterations")
    @classmethod
    def iterations_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_turn_iterations must be positive")
        return v

    model_config = {"extra": "allow"}


def merge_configs(base: dict, override: dict) -> dict:
    """Deep merge two dicts; override wins for leaf values."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_json_file(path: Path) -> dict:
    """Load a JSON file, returning empty dict if missing or invalid."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _parse_telemetry_config(raw: dict) -> TelemetryConfig:
    """Parse a telemetry config dict, falling back to LANGFUSE_* env vars."""
    import os

    return TelemetryConfig(
        enabled=raw.get("enabled", False),
        endpoint=raw.get("endpoint", "http://localhost:4318"),
        service_name=raw.get("service_name", "llm-code"),
        langfuse_public_key=raw.get("langfuse_public_key")
            or os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=raw.get("langfuse_secret_key")
            or os.environ.get("LANGFUSE_SECRET_KEY", ""),
        langfuse_host=raw.get("langfuse_host")
            or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )


def _dict_to_runtime_config(data: dict) -> RuntimeConfig:
    """Convert a merged config dict to a RuntimeConfig instance."""
    provider = data.get("provider", {})
    permissions = data.get("permissions", {})
    vision_raw = data.get("vision", {})

    hooks_raw = data.get("hooks", [])
    hooks = tuple(
        HookConfig(
            event=h["event"],
            command=h["command"],
            tool_pattern=h.get("tool_pattern", "*"),
            timeout=float(h.get("timeout", 10.0)),
            on_error=h.get("on_error", "warn"),
        )
        for h in hooks_raw
        if isinstance(h, dict) and "event" in h and "command" in h
    )

    vision = VisionConfig(
        fallback=vision_raw.get("fallback", ""),
        vision_model=vision_raw.get("vision_model", ""),
        vision_api=vision_raw.get("vision_api", ""),
        vision_api_key_env=vision_raw.get("vision_api_key_env", ""),
    )

    routing_raw = data.get("model_routing", {})
    _fallbacks_raw = routing_raw.get("fallbacks", ()) or ()
    if isinstance(_fallbacks_raw, str):
        _fallbacks_raw = (_fallbacks_raw,)
    model_routing = ModelRoutingConfig(
        sub_agent=routing_raw.get("sub_agent", ""),
        compaction=routing_raw.get("compaction", ""),
        fallback=routing_raw.get("fallback", ""),
        fallbacks=tuple(str(m) for m in _fallbacks_raw if m),
    )

    allow_tools = permissions.get("allow_tools", data.get("allowed_tools", []))
    deny_tools = permissions.get("deny_tools", data.get("denied_tools", []))

    voice_raw = data.get("voice", {})
    voice = VoiceConfig(
        enabled=voice_raw.get("enabled", False),
        backend=voice_raw.get("backend", "whisper"),
        whisper_url=voice_raw.get("whisper_url", "http://localhost:8000/v1/audio/transcriptions"),
        google_language_code=voice_raw.get("google_language_code", ""),
        anthropic_ws_url=voice_raw.get("anthropic_ws_url", "wss://api.anthropic.com"),
        language=voice_raw.get("language", "en"),
        hotkey=voice_raw.get("hotkey", "ctrl+space"),
        local_model=voice_raw.get("local_model", "base"),
        silence_seconds=float(voice_raw.get("silence_seconds", 2.0)),
        silence_threshold=int(voice_raw.get("silence_threshold", 500)),
    )

    thinking_raw = data.get("thinking", {})
    thinking = ThinkingConfig(
        mode=thinking_raw.get("mode", "adaptive"),
        budget_tokens=thinking_raw.get("budget_tokens", 10000),
    )

    dream_raw = data.get("dream", {})
    dream = DreamConfig(
        enabled=dream_raw.get("enabled", True),
        min_turns=dream_raw.get("min_turns", 3),
    )

    computer_use_raw = data.get("computer_use", {})
    computer_use = ComputerUseConfig(
        enabled=computer_use_raw.get("enabled", False),
        screenshot_delay=computer_use_raw.get("screenshot_delay", 0.5),
        app_tiers=tuple(computer_use_raw.get("app_tiers", [])),
    )

    ide_raw = data.get("ide", {})
    ide = IDEConfig(
        enabled=ide_raw.get("enabled", False),
        port=ide_raw.get("port", 9876),
    )

    swarm_raw = data.get("swarm", {})
    worktree_raw = swarm_raw.get("worktree", {})
    worktree = WorktreeConfig(
        on_complete=worktree_raw.get("on_complete", "diff"),
        base_dir=worktree_raw.get("base_dir", ""),
        copy_gitignored=tuple(worktree_raw.get("copy_gitignored", (".env", ".env.local"))),
        cleanup_on_success=worktree_raw.get("cleanup_on_success", True),
    )
    swarm = SwarmConfig(
        enabled=swarm_raw.get("enabled", False),
        backend=swarm_raw.get("backend", "auto"),
        max_members=swarm_raw.get("max_members", 5),
        role_models=swarm_raw.get("role_models", {}),
        worktree=worktree,
        overlap_threshold=float(swarm_raw.get("overlap_threshold", 0.6)),
        synthesis_enabled=bool(swarm_raw.get("synthesis_enabled", True)),
    )

    memory_raw = data.get("memory", {})
    memory = MemoryConfig(
        strict_derivable_check=bool(memory_raw.get("strict_derivable_check", False)),
        enabled=bool(memory_raw.get("enabled", True)),
        layer=str(memory_raw.get("layer", "in_memory")),
        hida_index_path=str(memory_raw.get("hida_index_path", "")),
        embedder=str(memory_raw.get("embedder", "sentence_transformers")),
        embedder_model=str(memory_raw.get("embedder_model", "all-MiniLM-L6-v2")),
        reranker=str(memory_raw.get("reranker", "cross_encoder_onnx")),
        reranker_model=str(
            memory_raw.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
        ),
        retrieve_top_k=int(memory_raw.get("retrieve_top_k", 20)),
        rerank_top_k=int(memory_raw.get("rerank_top_k", 5)),
        max_context_chars=int(memory_raw.get("max_context_chars", 4000)),
        default_scope=str(memory_raw.get("default_scope", "project")),
        remember_filter=str(memory_raw.get("remember_filter", "always")),
        context_template=str(memory_raw.get("context_template", "default")),
    )

    vcr_raw = data.get("vcr", {})
    vcr = VCRConfig(
        enabled=vcr_raw.get("enabled", False),
        auto_record=vcr_raw.get("auto_record", False),
    )

    hida_raw = data.get("hida", {})
    hida = HidaConfig(
        enabled=hida_raw.get("enabled", False),
        confidence_threshold=hida_raw.get("confidence_threshold", 0.6),
        custom_profiles=tuple(hida_raw.get("custom_profiles", [])),
    )

    telemetry = _parse_telemetry_config(data.get("telemetry", {}))

    enterprise_raw = data.get("enterprise", {})
    auth_raw = enterprise_raw.get("auth", {})
    rbac_raw = enterprise_raw.get("rbac", {})
    audit_raw = enterprise_raw.get("audit", {})
    oidc_raw = auth_raw.get("oidc", {})
    enterprise_auth = EnterpriseAuthConfig(
        provider=auth_raw.get("provider", ""),
        oidc_issuer=oidc_raw.get("issuer", ""),
        oidc_client_id=oidc_raw.get("client_id", ""),
        oidc_client_secret=oidc_raw.get("client_secret", ""),
        oidc_scopes=tuple(oidc_raw.get("scopes", ("openid", "email", "profile"))),
        oidc_redirect_port=oidc_raw.get("redirect_port", 9877),
    )
    enterprise_rbac = EnterpriseRBACConfig(
        group_role_mapping=rbac_raw.get("group_role_mapping", {}),
        custom_roles=rbac_raw.get("custom_roles", {}),
    )
    enterprise_audit = EnterpriseAuditConfig(
        retention_days=audit_raw.get("retention_days", 90),
    )
    enterprise = EnterpriseConfig(
        auth=enterprise_auth,
        rbac=enterprise_rbac,
        audit=enterprise_audit,
    )

    # Knowledge config
    knowledge_raw = data.get("knowledge", {})
    knowledge = KnowledgeConfig(
        enabled=knowledge_raw.get("enabled", True),
        compile_on_exit=knowledge_raw.get("compile_on_exit", True),
        max_context_tokens=knowledge_raw.get("max_context_tokens", 3000),
        compile_model=knowledge_raw.get("compile_model", ""),
    )

    # TUI config
    tui_raw = data.get("tui", {})
    tui = TuiConfig(
        spinner_verbs=tuple(tui_raw.get("spinner_verbs", ())),
        spinner_verbs_mode=tui_raw.get("spinner_verbs_mode", "append"),
    )

    # Harness config
    harness_data = data.get("harness", {})
    harness_controls: list[HarnessControl] = []
    for name, overrides in harness_data.get("controls", {}).items():
        harness_controls.append(HarnessControl(
            name=name,
            category=overrides.get("category", "sensor"),
            kind=overrides.get("kind", "computational"),
            enabled=overrides.get("enabled", True),
            trigger=overrides.get("trigger", "post_tool"),
        ))
    harness = HarnessConfig(
        template=harness_data.get("template", "auto"),
        controls=tuple(harness_controls),
    )

    return RuntimeConfig(
        model=data.get("model", ""),
        provider_base_url=provider.get("base_url", None),
        provider_api_key_env=provider.get("api_key_env", "LLM_API_KEY"),
        permission_mode=permissions.get("mode", data.get("permission_mode", "prompt")),
        max_turn_iterations=data.get("max_turn_iterations", 10),
        max_tokens=data.get("max_tokens", 4096),
        temperature=data.get("temperature", 0.7),
        hooks=hooks,
        allowed_tools=frozenset(allow_tools),
        denied_tools=frozenset(deny_tools),
        compact_after_tokens=data.get("compact_after_tokens", 80000),
        timeout=data.get("timeout", 120.0),
        max_retries=data.get("max_retries", 2),
        native_tools=data.get("native_tools", True),
        vision=vision,
        model_routing=model_routing,
        mcp_servers=(
            # Back-compat flat view: always_on entries only (what the TUI
            # currently starts at init). on_demand servers are hidden from
            # the flat view so the TUI auto-start loop skips them.
            #
            # v2.5.3 — also accept ``mcp_servers`` (snake_case) for users
            # whose configs were written by the pre-v2.5.3 ``/mcp install``
            # bug. ``mcpServers`` (camelCase) wins on collision; otherwise
            # the snake-case entries are merged in.
            _parse_mcp_config(
                {**data.get("mcp_servers", {}), **data.get("mcpServers", {})}
            ).always_on
        ),
        mcp=_parse_mcp_config(
            {**data.get("mcp_servers", {}), **data.get("mcpServers", {})}
        ),
        registries=data.get("registries", {}),
        skills_dirs=tuple(data.get("skills_dirs", [])),
        lsp_servers=data.get("lspServers", {}),
        lsp_auto_detect=data.get("lsp_auto_detect", True),
        model_aliases=data.get("model_aliases", {}),
        pricing=data.get("pricing", {}),
        thinking=thinking,
        vim_mode=data.get("vim_mode", False),
        voice=voice,
        dream=dream,
        computer_use=computer_use,
        ide=ide,
        swarm=swarm,
        memory=memory,
        vcr=vcr,
        hida=hida,
        telemetry=telemetry,
        max_budget_usd=data.get("max_budget_usd", None),
        enterprise=enterprise,
        output_compression=data.get("output_compression", True),
        auto_commit=data.get("auto_commit", False),
        lsp_auto_diagnose=data.get("lsp_auto_diagnose", True),
        harness=harness,
        knowledge=knowledge,
        tui=tui,
    )


def load_config(
    user_dir: Path,
    project_dir: Path,
    local_path: Path,
    cli_overrides: dict,
) -> RuntimeConfig:
    """Load from JSON files in order, deep merge, convert to RuntimeConfig.

    Precedence (lowest to highest):
      user_dir/config.json -> project_dir/config.json -> local_path -> cli_overrides
    """
    merged: dict = {}

    user_cfg = _load_json_file(Path(user_dir) / "config.json")
    merged = merge_configs(merged, user_cfg)

    project_cfg = _load_json_file(Path(project_dir) / "config.json")
    merged = merge_configs(merged, project_cfg)

    local_cfg = _load_json_file(Path(local_path))
    merged = merge_configs(merged, local_cfg)

    merged = merge_configs(merged, cli_overrides)

    # Apply pending config migrations
    from llm_code.runtime.config_migration import apply_pending_migrations
    merged = apply_pending_migrations(merged, config_dir=Path(user_dir))

    # Validate merged config; on error, log warning and continue with best-effort defaults
    try:
        ConfigSchema.model_validate(merged)
    except ValidationError as exc:
        import warnings
        warnings.warn(f"Config validation error (continuing with defaults): {exc}", stacklevel=2)
        print(f"[WARNING] Config validation error: {exc}", file=sys.stderr)

    return _dict_to_runtime_config(merged)

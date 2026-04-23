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

    # ── v13 Phase A: profile-driven adapters ──────────────────────────
    # Replace the hardcoded if-ladder in ``runtime/prompt.py::
    # select_intro_prompt`` (deleted in v13 Phase C). A profile that
    # omits these fields gets the historical fallback via the
    # deprecated shim — so Phase A is a zero-behaviour-change refactor.
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
    section_map = {
        "provider": "provider_type",
        "streaming": ("implicit_thinking", "reasoning_field"),
        "thinking": ("thinking_extra_body_format", "default_thinking_budget"),
        "pricing": ("price_input", "price_output"),
        "limits": ("max_output_tokens", "context_window"),
        "sampling": ("default_temperature", "reasoning_effort"),
        "deployment": ("is_local", "unlimited_token_upgrade", "is_small_model"),
        "routing": ("tier_c_model",),
        # v13 Phase A sections.
        "prompt": ("prompt_template", "prompt_match"),
        "parser": ("parser_variants",),
        "parser_hints": ("custom_close_tags", "call_separator_chars"),
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
                # Single-field section (e.g. [provider] type → provider_type)
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

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
from dataclasses import dataclass, field, fields
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

    # ── Pricing (per 1M tokens) ───────────────────────────────────────
    price_input: float = 0.0  # 0 = unknown / free
    price_output: float = 0.0

    # ── Limits ────────────────────────────────────────────────────────
    max_output_tokens: int = 4096
    context_window: int = 128000  # advertised context length


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
        context_window=32768,
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
        context_window=131072,
    ),
}

_DEFAULT_PROFILE = ModelProfile(name="(default)")


# ── TOML loading ──────────────────────────────────────────────────────


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return the parsed dict."""
    import tomllib
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

    # Merge: base fields + overrides from flat
    merged = {}
    for f in fields(ModelProfile):
        if f.name in flat:
            merged[f.name] = flat[f.name]
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
        self._load_user_profiles()

    def _load_user_profiles(self) -> None:
        """Load *.toml files from the user profile directory."""
        if not self._user_dir.is_dir():
            return
        for toml_path in sorted(self._user_dir.glob("*.toml")):
            try:
                data = _load_toml(toml_path)
                # Profile key is the filename without extension
                key = toml_path.stem.lower()
                # Find base profile to merge over
                base = self._profiles.get(key, _DEFAULT_PROFILE)
                profile = _profile_from_dict(data, base=base)
                self._profiles[key] = profile
                _logger.debug("loaded user profile: %s from %s", key, toml_path)
            except Exception as exc:
                _logger.warning("failed to load profile %s: %s", toml_path, exc)

    def resolve(self, model: str) -> ModelProfile:
        """Resolve a model name to its profile."""
        key = model.lower()

        # 1. Exact match
        if key in self._profiles:
            return self._profiles[key]

        # 2. Prefix match (longest prefix wins)
        best_match: str = ""
        for profile_key in self._profiles:
            if key.startswith(profile_key) and len(profile_key) > len(best_match):
                best_match = profile_key
        if best_match:
            return self._profiles[best_match]

        # 3. Family defaults
        for family_prefix, family_profile in _FAMILY_DEFAULTS.items():
            if key.startswith(family_prefix):
                return family_profile

        # 4. Default
        return _DEFAULT_PROFILE

    def list_profiles(self) -> dict[str, ModelProfile]:
        """Return all registered profiles (built-in + user)."""
        return dict(self._profiles)


# ── Module-level singleton ────────────────────────────────────────────

_registry: ProfileRegistry | None = None


def get_profile(model: str) -> ModelProfile:
    """Resolve a model name to its profile using the global registry.

    The registry is lazily initialized on first call. Thread-safe for
    reads after initialization (frozen dataclasses, dict lookup).
    """
    global _registry
    if _registry is None:
        _registry = ProfileRegistry()
    return _registry.resolve(model)


def get_registry() -> ProfileRegistry:
    """Return the global ProfileRegistry, initializing if needed."""
    global _registry
    if _registry is None:
        _registry = ProfileRegistry()
    return _registry

"""Model alias resolution."""
from __future__ import annotations

# Built-in aliases
BUILTIN_ALIASES: dict[str, str] = {
    # Short names
    "gpt4o": "gpt-4o",
    "gpt4": "gpt-4o",
    "gpt-mini": "gpt-4o-mini",
    "4o": "gpt-4o",
    "4o-mini": "gpt-4o-mini",
    "o3": "o3",
    "o4-mini": "o4-mini",
    # Anthropic
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "claude": "claude-sonnet-4-6",
    # Qwen shortcuts
    "qwen": "qwen3.5",
    "qwen-large": "Qwen3.5-122B-A10B",
}


def resolve_model(model: str, custom_aliases: dict[str, str] | None = None) -> str:
    """Resolve a model alias to its full name.

    Priority: custom_aliases (from config) > BUILTIN_ALIASES > return as-is
    """
    if custom_aliases and model in custom_aliases:
        return custom_aliases[model]
    if model in BUILTIN_ALIASES:
        return BUILTIN_ALIASES[model]
    return model

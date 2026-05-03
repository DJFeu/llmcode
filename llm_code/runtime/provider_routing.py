"""Provider-map model routing helpers.

Supports opencode-style ``provider/model`` refs while preserving the
legacy single-provider config. A slash model is only split when the
first segment matches a configured provider id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from llm_code.runtime.model_aliases import resolve_model

if TYPE_CHECKING:
    from llm_code.api.provider import LLMProvider
    from llm_code.runtime.model_profile import ModelProfile
    from llm_code.runtime.config import RuntimeConfig


@dataclass(frozen=True)
class ProviderTarget:
    """Resolved request target for a logical model ref."""

    logical_model: str
    request_model: str
    provider_id: str
    base_url: str
    api_key_env: str
    api_key: str = ""
    uses_provider_map: bool = False


def resolve_provider_target(
    config: "RuntimeConfig",
    model: str | None = None,
) -> ProviderTarget:
    """Resolve ``model`` to request model + provider endpoint.

    ``provider/model`` parsing is intentionally conservative: the
    provider id must exist in ``config.provider_map``. Unknown slash
    models remain untouched for legacy HuggingFace/path ids.
    """
    raw_model = model if model is not None else getattr(config, "model", "")
    logical_model = resolve_model(
        raw_model,
        custom_aliases=getattr(config, "model_aliases", {}) or None,
    )
    provider_map = getattr(config, "provider_map", {}) or {}

    provider_id = ""
    request_model = logical_model
    if "/" in logical_model:
        head, rest = logical_model.split("/", 1)
        if head in provider_map and rest:
            provider_id = head
            request_model = rest

    if provider_id:
        provider_cfg = provider_map[provider_id]
        return ProviderTarget(
            logical_model=logical_model,
            request_model=request_model,
            provider_id=provider_id,
            base_url=provider_cfg.base_url,
            api_key_env=provider_cfg.api_key_env
            or getattr(config, "provider_api_key_env", "LLM_API_KEY"),
            api_key=provider_cfg.api_key,
            uses_provider_map=True,
        )

    return ProviderTarget(
        logical_model=logical_model,
        request_model=request_model,
        provider_id="",
        base_url=getattr(config, "provider_base_url", "") or "",
        api_key_env=getattr(config, "provider_api_key_env", "LLM_API_KEY"),
        api_key="",
        uses_provider_map=False,
    )


def resolve_api_key_for_target(target: ProviderTarget) -> str:
    """Resolve a target API key without logging or printing secrets."""
    from llm_code.runtime.auth import resolve_api_key

    if target.api_key_env:
        resolved = resolve_api_key(target.api_key_env)
        if resolved:
            return resolved
    return target.api_key


def resolve_profile_for_target(target: ProviderTarget) -> "ModelProfile":
    """Resolve profile using the logical provider ref before request model.

    Provider-map users may need endpoint-specific profiles such as
    ``planner/deepseek`` even though the API payload model is only
    ``deepseek``. If no logical profile exists, fall back to the request
    model so built-in profiles keep working.
    """
    from llm_code.runtime.model_profile import get_profile

    profile = get_profile(target.logical_model)
    if (
        profile.name == "(default)"
        and target.request_model
        and target.request_model != target.logical_model
    ):
        profile = get_profile(target.request_model)
    return profile


def create_provider_for_model(
    config: "RuntimeConfig",
    model: str | None = None,
    *,
    native_tools: bool | None = None,
) -> "LLMProvider":
    """Build a provider client for ``model`` using its routed endpoint."""
    from llm_code.api.client import ProviderClient

    target = resolve_provider_target(config, model)
    return ProviderClient.from_model(
        model=target.request_model,
        base_url=target.base_url,
        api_key=resolve_api_key_for_target(target),
        timeout=getattr(config, "timeout", 120.0),
        max_retries=getattr(config, "max_retries", 2),
        native_tools=(
            getattr(config, "native_tools", True)
            if native_tools is None else native_tools
        ),
    )

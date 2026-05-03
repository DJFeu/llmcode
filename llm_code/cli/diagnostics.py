"""Multi-model diagnostics CLI commands.

These commands are intentionally read-only: they explain the effective
configuration, provider/profile pairing, and remote model inventory without
starting the interactive runtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
import httpx

from llm_code.api.provider_registry import resolve_descriptor_for_model
from llm_code.runtime.config import ConfigLoadResult, load_config_with_provenance
from llm_code.runtime.model_profile import get_profile

__all__ = ["config_group", "doctor", "models_group"]


def _config_paths(cwd: Path) -> tuple[Path, Path, Path]:
    project_config_dir = cwd / ".llmcode"
    return (
        Path.home() / ".llmcode",
        project_config_dir,
        project_config_dir / "config.local.json",
    )


def _load_current_config() -> ConfigLoadResult:
    user_dir, project_dir, local_path = _config_paths(Path.cwd())
    return load_config_with_provenance(
        user_dir=user_dir,
        project_dir=project_dir,
        local_path=local_path,
        cli_overrides={},
    )


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            out.update(_flatten(value, dotted))
        else:
            out[dotted] = value
    return out


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if value is None:
        return "null"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


@click.group(name="config")
def config_group() -> None:
    """Inspect llmcode configuration."""


@config_group.command("explain")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def config_explain(json_output: bool) -> None:
    """Show the effective merged config and winning source per key."""
    result = _load_current_config()
    flat = _flatten(result.raw)

    if json_output:
        payload = {
            key: {
                "value": value,
                "source": result.sources.get(key).label
                if result.sources.get(key) else "default",
                "path": result.sources.get(key).path
                if result.sources.get(key) else "",
            }
            for key, value in sorted(flat.items())
        }
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    click.echo("Effective config:")
    if not flat:
        click.echo("  (no config files or CLI overrides; using defaults)")
        return

    width = max(len(key) for key in flat)
    for key, value in sorted(flat.items()):
        src = result.sources.get(key)
        label = src.label if src is not None else "default"
        path = f" ({src.path})" if src is not None and src.path else ""
        click.echo(
            f"  {key.ljust(width)}  = {_display_value(value)}"
            f"  source={label}{path}"
        )


@click.command(name="doctor")
def doctor() -> None:
    """Check the active model/profile/provider configuration."""
    result = _load_current_config()
    cfg = result.config
    from llm_code.runtime.provider_routing import (
        resolve_profile_for_target,
        resolve_provider_target,
    )
    target = resolve_provider_target(cfg) if cfg.model else None
    model = target.logical_model if target is not None else "(not set)"
    request_model = target.request_model if target is not None else ""
    profile = resolve_profile_for_target(target) if target is not None else None
    descriptor = resolve_descriptor_for_model(request_model) if request_model else None

    click.echo("llmcode doctor")
    click.echo("")
    click.echo(f"model: {model}")
    if target is not None and target.uses_provider_map:
        click.echo(f"provider id: {target.provider_id}")
        click.echo(f"request model: {target.request_model}")
    if profile is None:
        click.echo("profile: missing (set `model` in config or pass --model)")
    else:
        click.echo(f"profile: {profile.name or '(unnamed)'}")
        click.echo(f"provider_type: {profile.provider_type}")
        caps = []
        if profile.native_tools:
            caps.append("native-tools")
        if profile.force_xml_tools:
            caps.append("xml-tools")
        if profile.supports_reasoning:
            caps.append("reasoning")
        if profile.supports_images:
            caps.append("images")
        click.echo(f"model capabilities: {', '.join(caps) if caps else 'none'}")
        click.echo(f"context_window: {profile.context_window}")

    if descriptor is None:
        click.echo("provider descriptor: missing")
    else:
        click.echo(f"provider descriptor: {descriptor.provider_type}")
        click.echo(f"provider tools_format: {descriptor.capabilities.tools_format}")

    if target is not None and target.base_url:
        click.echo(f"provider.base_url: {target.base_url}")
    else:
        click.echo("provider.base_url: not set")

    key_env = target.api_key_env if target is not None else cfg.provider_api_key_env
    if key_env:
        state = "set" if os.environ.get(key_env) else "not set"
        click.echo(f"provider.api_key_env: {key_env} ({state})")
    if cfg.provider_map:
        click.echo("provider.map: " + ", ".join(sorted(cfg.provider_map)))

    if cfg.model_routing.fallbacks:
        click.echo(
            "fallbacks: " + " -> ".join(cfg.model_routing.fallbacks)
        )
    elif cfg.model_routing.fallback:
        click.echo(f"fallback: {cfg.model_routing.fallback}")


@click.group(name="models")
def models_group() -> None:
    """Inspect provider model inventory."""


@models_group.command("probe")
@click.option("--api", "api_base", default=None, help="OpenAI-compatible base URL.")
@click.option("--timeout", type=float, default=3.0, show_default=True)
def models_probe(api_base: str | None, timeout: float) -> None:
    """Probe an OpenAI-compatible /v1/models endpoint."""
    if not api_base:
        from llm_code.runtime.provider_routing import resolve_provider_target
        api_base = resolve_provider_target(_load_current_config().config).base_url
    if not api_base:
        raise click.UsageError(
            "No provider base URL. Pass --api or set provider.base_url."
        )

    url = _models_url(api_base)
    try:
        response = httpx.get(url, timeout=timeout)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error probing {url}: {exc}", err=True)
        raise SystemExit(1) from exc

    models = data.get("data", []) if isinstance(data, dict) else []
    click.echo(f"Models at {api_base.rstrip('/')}:")
    if not models:
        click.echo("  (no models returned)")
        return

    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            continue
        profile = get_profile(model_id)
        context = entry.get("max_model_len") or entry.get("context_length") or ""
        context_text = str(context) if context else str(profile.context_window)
        mode = "native" if profile.native_tools and not profile.force_xml_tools else "xml"
        click.echo(
            f"  {model_id}  context={context_text}  "
            f"profile={profile.name or '(default)'}  "
            f"provider={profile.provider_type}  tools={mode}"
        )

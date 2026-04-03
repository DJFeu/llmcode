"""Runtime configuration dataclasses and loader."""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator


@dataclass(frozen=True)
class HookConfig:
    event: str          # "pre_tool_use" | "post_tool_use" | "on_stop"
    command: str
    tool_pattern: str = "*"


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


@dataclass(frozen=True)
class ThinkingConfig:
    mode: str = "adaptive"        # "adaptive" | "enabled" | "disabled"
    budget_tokens: int = 10000


@dataclass(frozen=True)
class DreamConfig:
    enabled: bool = True
    min_turns: int = 3


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    backend: str = "whisper"  # "whisper" | "google" | "anthropic"
    whisper_url: str = "http://localhost:8000/v1/audio/transcriptions"
    google_language_code: str = ""
    anthropic_ws_url: str = "wss://api.anthropic.com"
    language: str = "en"
    hotkey: str = "ctrl+space"


@dataclass(frozen=True)
class ComputerUseConfig:
    enabled: bool = False
    screenshot_delay: float = 0.5


@dataclass(frozen=True)
class IDEConfig:
    enabled: bool = False
    port: int = 9876


@dataclass(frozen=True)
class SwarmConfig:
    enabled: bool = False
    backend: str = "auto"       # "auto" | "tmux" | "subprocess"
    max_members: int = 5


@dataclass(frozen=True)
class VCRConfig:
    enabled: bool = False
    auto_record: bool = False


@dataclass(frozen=True)
class HidaConfig:
    enabled: bool = False
    confidence_threshold: float = 0.6
    custom_profiles: tuple[dict, ...] = ()


@dataclass(frozen=True)
class RuntimeConfig:
    model: str = ""
    provider_base_url: str | None = None
    provider_api_key_env: str = "LLM_API_KEY"
    permission_mode: str = "prompt"
    max_turn_iterations: int = 10
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
    vcr: VCRConfig = field(default_factory=VCRConfig)
    hida: HidaConfig = field(default_factory=HidaConfig)


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
    max_turn_iterations: int = 10
    thinking: dict = {}
    max_tokens: int = 4096
    temperature: float = 0.7
    compact_after_tokens: int = 80000
    native_tools: bool = True

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
    model_routing = ModelRoutingConfig(
        sub_agent=routing_raw.get("sub_agent", ""),
        compaction=routing_raw.get("compaction", ""),
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
    )

    ide_raw = data.get("ide", {})
    ide = IDEConfig(
        enabled=ide_raw.get("enabled", False),
        port=ide_raw.get("port", 9876),
    )

    swarm_raw = data.get("swarm", {})
    swarm = SwarmConfig(
        enabled=swarm_raw.get("enabled", False),
        backend=swarm_raw.get("backend", "auto"),
        max_members=swarm_raw.get("max_members", 5),
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
        mcp_servers=data.get("mcpServers", {}),
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
        vcr=vcr,
        hida=hida,
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

    # Validate merged config; on error, report and continue with best-effort defaults
    try:
        ConfigSchema.model_validate(merged)
    except ValidationError as exc:
        print(f"Config validation error: {exc}", file=sys.stderr)

    return _dict_to_runtime_config(merged)

"""JSON schema for ``hayhooks.toml`` / the ``hayhooks`` config block.

Mirrors :class:`llm_code.runtime.config.HayhooksConfig`. Consumed by
``llmcode hayhooks serve --config-schema`` and by documentation
generation in ``docs/hayhooks/``.
"""
from __future__ import annotations

HAYHOOKS_CONFIG_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "llm_code/hayhooks/config_schema.py",
    "title": "HayhooksConfig",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {
            "type": "boolean",
            "default": False,
            "description": "Global toggle. When false, `hayhooks serve` refuses to start.",
        },
        "auth_token_env": {
            "type": "string",
            "default": "LLMCODE_HAYHOOKS_TOKEN",
            "description": "Env var holding the bearer token.",
        },
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "Whitelist of tools the hayhooks agent may call. Empty = all.",
        },
        "max_agent_steps": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "default": 20,
        },
        "request_timeout_s": {
            "type": "number",
            "minimum": 1,
            "maximum": 3600,
            "default": 300.0,
        },
        "rate_limit_rpm": {
            "type": "integer",
            "minimum": 0,
            "default": 60,
            "description": "Requests per minute per session fingerprint (0 disables).",
        },
        "enable_openai_compat": {
            "type": "boolean",
            "default": True,
        },
        "enable_mcp": {
            "type": "boolean",
            "default": True,
        },
        "enable_ide_rpc": {
            "type": "boolean",
            "default": True,
            "description": "Serve the IDE JSON-RPC WebSocket endpoint (absorbed from llm_code.ide in M4.11).",
        },
        "enable_debug_repl": {
            "type": "boolean",
            "default": False,
            "description": "Serve the legacy debug REPL WebSocket endpoint.",
        },
        "cors_origins": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "host": {
            "type": "string",
            "default": "127.0.0.1",
            "description": "Bind address. Any non-loopback value requires --allow-remote.",
        },
        "port": {
            "type": "integer",
            "minimum": 0,
            "maximum": 65535,
            "default": 8080,
        },
    },
}


def schema() -> dict:
    """Return a deep-copy of the hayhooks JSON schema."""
    import copy
    return copy.deepcopy(HAYHOOKS_CONFIG_SCHEMA)

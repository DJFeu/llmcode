# Configuration

## Config Locations

| Location | Scope | Precedence |
|----------|-------|------------|
| `~/.llmcode/config.json` | User global | Low |
| `.llmcode/config.json` | Project | Medium |
| `.llmcode/config.local.json` | Local (gitignore) | High |
| CLI flags | Session | Highest |

Inspect the merged result with:

```bash
llmcode config explain
llmcode doctor
```

`config explain` prints each configured key with the winning source layer. `doctor` resolves the active model profile and provider descriptor so provider/profile mismatches are visible before starting a turn.

## Full Reference

```json
{
  "model": "qwen3.5-122b",
  "provider": {
    "base_url": "http://localhost:8000/v1",
    "api_key_env": "LLM_API_KEY",
    "timeout": 120,
    "max_retries": 2
  },
  "permissions": {
    "mode": "prompt",
    "allow_tools": [],
    "deny_tools": []
  },
  "model_routing": {
    "sub_agent": "qwen3.5-32b",
    "compaction": "qwen3.5-7b"
  },
  "vision": {
    "vision_model": "qwen2.5-vl-7b",
    "vision_api": "http://localhost:8001/v1"
  },
  "mcpServers": {},
  "lspServers": {},
  "hooks": [],
  "registries": {}
}
```

## Permission Modes

| Mode | Behavior |
|------|----------|
| `prompt` | Ask for elevated operations |
| `auto_accept` | Allow everything |
| `read_only` | Only read operations |
| `workspace_write` | Read + write, no shell |
| `full_access` | Everything allowed |

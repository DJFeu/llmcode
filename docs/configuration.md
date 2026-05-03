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
  "model": "planner/deepseek",
  "small_model": "worker/llama",
  "provider": {
    "planner": {
      "name": "DeepSeek-R1 local",
      "options": {
        "baseURL": "https://deepseek.example.com/v1",
        "apiKey": "{env:LOCAL_LLM_API_KEY}"
      },
      "models": {
        "deepseek": { "name": "DeepSeek-R1" }
      }
    },
    "worker": {
      "name": "Llama-3.3 local",
      "options": {
        "baseURL": "https://llama.example.com/v1",
        "apiKey": "{env:LOCAL_LLM_API_KEY}"
      },
      "models": {
        "llama": { "name": "Llama-3.3 70B Instruct" }
      }
    }
  },
  "permissions": {
    "mode": "prompt",
    "allow_tools": [],
    "deny_tools": []
  },
  "model_routing": {
    "sub_agent": "worker/llama",
    "compaction": "worker/llama",
    "fallback": "planner/deepseek"
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

Use the legacy single-provider shape when every model shares one endpoint:

```json
{
  "model": "local-coder",
  "provider": {
    "base_url": "http://localhost:8000/v1",
    "api_key_env": "LLM_API_KEY"
  }
}
```

For custom profiles that belong to one provider-map endpoint, keep `[prompt].match`
specific to the logical ref:

```toml
[prompt]
template = "llama"
match = ["worker/llama"]
```

This keeps a local endpoint profile from overriding every model whose id happens
to contain `llama` or `deepseek`. If no logical-ref profile matches, llmcode falls
back to the request model id.

The `provider/model` form is only split when `provider` contains a matching
provider id. Unknown slash-based model ids are left untouched for backward
compatibility.

## Web RAG Preflight

For local models, prompts that explicitly need current or external knowledge
(`today`, `latest`, `news`, `search`, `release note`, prices, laws, weather,
and similar wording) trigger a Web RAG preflight before the first model call.
The runtime runs `web_search`, fetches selected source pages when `web_fetch`
is available, removes weak homepage/directory results and JavaScript-heavy
fetch output, then injects the curated context into the system prompt.

When this preflight succeeds, `web_search` and `web_fetch` are hidden for that
model call so the model answers from the curated evidence instead of issuing a
second lower-quality search. Pure coding, refactor, algorithm, DFS/BFS, and
static CS prompts do not trigger the preflight.

## Permission Modes

| Mode | Behavior |
|------|----------|
| `prompt` | Ask for elevated operations |
| `auto_accept` | Allow everything |
| `read_only` | Only read operations |
| `workspace_write` | Read + write, no shell |
| `full_access` | Everything allowed |

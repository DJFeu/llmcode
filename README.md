# llm-code

A production-grade CLI coding agent for local LLMs. Delivers a Claude Code-level developer experience with any OpenAI-compatible or Anthropic model.

## Features

- **20+ built-in tools** — file read/write/edit, bash, glob, grep, 7 git tools, LSP, memory
- **Multi-provider** — OpenAI-compatible (vLLM, Ollama, LM Studio) + Anthropic
- **MCP ecosystem** — Connect any MCP server (GitHub, Postgres, filesystem, 500+)
- **Plugin marketplace** — Claude Code plugin.json compatible, multi-registry search
- **Skills system** — Auto-inject or slash-command triggered workflow prompts
- **Sub-agents** — Parallel child agents with specialized roles (Explore, Plan, Verify)
- **Smart safety** — Input-aware permission (bash ls auto-approved, rm needs confirmation)
- **Session memory** — Cross-session persistent notes + auto session summaries
- **Streaming UI** — Real-time Markdown rendering + tool progress indicators
- **Git integration** — 7 git tools + auto checkpoint before writes + /undo
- **LSP integration** — Go-to-definition, find-references, diagnostics
- **Context management** — 4-level compression + prefix cache + token/result budgets

## Quick Start

### Install

```bash
pip install llm-code
```

### Configure

```bash
mkdir -p ~/.llm-code
cat > ~/.llm-code/config.json << 'EOF'
{
  "model": "qwen3.5-122b",
  "provider": {
    "base_url": "http://localhost:8000/v1"
  },
  "permissions": {
    "mode": "prompt"
  }
}
EOF
```

### Run

```bash
# Interactive REPL
llm-code

# One-shot
llm-code "fix the bug in main.py"

# With model override
llm-code --model gpt-4o --api https://api.openai.com/v1 "explain this code"

# Pipe
cat error.log | llm-code "explain this error"

# With token budget
llm-code --budget 500000 "refactor the auth module"
```

## Configuration

### Config file locations (precedence low to high)

1. `~/.llm-code/config.json` — User global
2. `.llm-code/config.json` — Project
3. `.llm-code/config.local.json` — Local (gitignored)
4. CLI flags / env vars — Highest

### Full config example

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
    "allow_tools": ["read_file", "glob_search", "grep_search"],
    "deny_tools": []
  },
  "model_routing": {
    "sub_agent": "qwen3.5-32b",
    "compaction": "qwen3.5-7b"
  },
  "vision": {
    "fallback": "vision_model",
    "vision_model": "qwen2.5-vl-7b",
    "vision_api": "http://localhost:8001/v1"
  },
  "hooks": [
    {"event": "post_tool_use", "tool_pattern": "write_file|edit_file", "command": "ruff format {path}"}
  ],
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "ghp_xxx"}
    }
  },
  "registries": {
    "official": {"type": "official", "enabled": true}
  }
}
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show commands |
| `/clear` | Clear conversation |
| `/model <name>` | Switch model |
| `/session list\|save\|switch` | Manage sessions |
| `/config set <key> <value>` | Modify config |
| `/plugin list\|search\|enable\|disable` | Manage plugins |
| `/skill` | List available skills |
| `/memory` | List project memory |
| `/index` | Show project index |
| `/undo` | Undo last file change |
| `/budget <tokens>` | Set token budget |
| `/cost` | Token usage |
| `/cd <dir>` | Change directory |
| `/image <path>` | Load image |
| `/exit` | Exit |

## Tools

### Built-in (20)

**File Operations:** read_file, write_file, edit_file, glob_search, grep_search, bash

**Git:** git_status, git_diff, git_log, git_commit, git_push, git_stash, git_branch

**Agent:** agent (with roles: explore, plan, verify)

**Memory:** memory_store, memory_recall, memory_list

**LSP:** lsp_goto_definition, lsp_find_references, lsp_diagnostics

### MCP Tools

Any MCP server's tools are automatically registered. Tools are named `mcp__{server}__{tool}`.

## Architecture

```
llm_code/
├── api/          # Provider abstraction (OpenAI-compat + Anthropic)
├── tools/        # 20 builtin tools + agent + parsing
├── runtime/      # Conversation engine, permissions, hooks, session, memory
├── mcp/          # MCP client, bridge, server manager
├── marketplace/  # Plugin system, 5 registries
├── lsp/          # LSP client, auto-detector, tools
└── cli/          # REPL, rendering, commands, streaming
```

## Development

```bash
git clone https://github.com/user/llm-code
cd llm-code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT

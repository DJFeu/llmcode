# llm-code

<p align="center">
  <strong>Open-source CLI coding agent for any LLM</strong><br>
  Claude Code-level developer experience with local or cloud models
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#marketplace">Marketplace</a> ·
  <a href="#features">Features</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#architecture">Architecture</a>
</p>

---

## What is llm-code?

A production-grade terminal coding agent that works with **any LLM** — local (Qwen, Llama, Mistral via vLLM/Ollama) or cloud (OpenAI, Anthropic, xAI, DeepSeek). One tool, any model.

```
  _     _     __  __      Local LLM Agent
 | |   | |   |  \/  |     ─────────────────────────────
 | |   | |   | |\/| |     Model        qwen3.5-122b
 | |__ | |__ | |  | |     Workspace    my-project · main
 |____||____||_|  |_|     Directory    ~/my-project
   ____  ___  ____  ___   Permissions  prompt
  / ___||   \|  _ \| __|
 | |    | |) | | | | _|   Quick start  /help · /skill · /mcp
 | |___ |   /| |_| | |__  Multiline    Shift+Enter
  \____||___\|____/|____|  Images       Cmd+V pastes
```

## Quick Start

```bash
# Install
pip install llm-code

# Configure
mkdir -p ~/.llm-code
cat > ~/.llm-code/config.json << 'EOF'
{
  "model": "qwen3.5",
  "provider": {
    "base_url": "http://localhost:8000/v1"
  }
}
EOF

# Run
llm-code
```

### Model Aliases

Use short names — llm-code resolves them automatically:

```json
{
  "model": "qwen",
  "model_aliases": {
    "qwen": "/models/Qwen3.5-122B-A10B-int4-AutoRound",
    "fast": "qwen3.5-7b",
    "gpt": "gpt-4o"
  }
}
```

Built-in aliases: `opus`, `sonnet`, `haiku`, `gpt4o`, `gpt-mini`, `qwen`, `o3`

## Marketplace

llm-code connects to **three marketplace sources** with 44,000+ skills and plugins available:

### Skills — `/skill`

Browse and install skills with an interactive React selector:

```
Skills (14 installed + 91 available)
↑↓ navigate · Enter select · Esc close

 ❯ ● brainstorming  · ~2588 tokens (installed)
   ● test-driven-development  · ~2431 tokens (installed)
   ○ claude-code-skill-security-check  · [npm] ...
   ○ clawhub:code-review-fix  · [ClawHub] ...
   ↓ 80 more below
```

| Source | Skills | Description |
|--------|-------:|-------------|
| **ClawHub.ai** | 44,000+ | Largest skill marketplace for AI agents |
| **npm** | 50+ | Claude Code official skill packages |
| **Local plugins** | varies | Skills from installed plugins (e.g., superpowers) |

### Plugins — `/plugin`

Plugins bundle skills, hooks, MCP servers, and agents:

```bash
# Install from GitHub
/plugin install obra/superpowers    # 14 workflow skills

# Browse marketplace (Official + ClawHub + npm)
/plugin
```

| Source | Plugins | Description |
|--------|--------:|-------------|
| **Official** | 28 | Claude Code official plugins (data-engineering, figma, playwright...) |
| **ClawHub** | 50+ | Community plugins (memory engines, security, integrations) |
| **npm** | 20+ | npm-distributed plugins |

### MCP Servers — `/mcp`

Connect any [MCP](https://modelcontextprotocol.io/) server to extend tools:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "ghp_xxx"}
    }
  }
}
```

Supports **stdio**, **HTTP**, **SSE**, and **WebSocket** transports. MCP server instructions are auto-injected into the system prompt.

## Features

### Core Agent
- **20+ built-in tools** — read/write/edit files, bash, glob, grep, 7 git tools, LSP, memory
- **Multi-provider** — OpenAI-compatible (vLLM, Ollama, LM Studio) + Anthropic + xAI
- **Dual-track tool calling** — native function calling when available, XML tag fallback for any model
- **Sub-agents** — parallel child agents with specialized roles (Explore, Plan, Verify)
- **Streaming** — real-time Markdown rendering + tool progress indicators

### Smart Safety
- **Input-aware permissions** — `bash ls` auto-approved, `rm -rf` needs confirmation
- **5 permission modes** — read_only, workspace_write, full_access, prompt, auto_accept
- **Hook system** — pre/post tool-use hooks with exit code semantics
- **Git checkpoint** — auto-checkpoint before writes, `/undo` to restore

### Context Management
- **4-level compression** — snip → micro → collapse → auto (progressive)
- **Prefix cache optimization** — prompt ordering for vLLM 2-5x speedup
- **Token budget** — `--budget 500000` to control spending
- **Tool result budget** — large outputs persisted to disk, summaries in context

### Developer Experience
- **React+Ink UI** — interactive marketplace, tool panels, syntax highlighting
- **Image support** — Cmd+V paste from clipboard, drag-and-drop
- **Cross-session memory** — persistent notes + auto session summaries
- **Project indexing** — file tree + symbol index for smarter context
- **LSP integration** — go-to-definition, find-references, diagnostics
- **Cost tracking** — per-model pricing with custom config

### Remote Execution
- **Server mode** — `llm-code --serve` (WebSocket JSON-RPC)
- **Client mode** — `llm-code --connect host:8765`
- **SSH proxy** — `llm-code --ssh user@host` (auto-tunnel)

## Modes

```bash
llm-code                           # Default: React+Ink UI
llm-code --lite                    # Lightweight print-based CLI
llm-code --serve --port 8765       # Remote server
llm-code --connect host:8765       # Remote client
llm-code --ssh user@host           # SSH tunnel + connect
```

## Configuration

### Config Locations (precedence low → high)

1. `~/.llm-code/config.json` — User global
2. `.llm-code/config.json` — Project
3. `.llm-code/config.local.json` — Local (gitignored)
4. CLI flags / env vars — Highest

### Full Config Example

```json
{
  "model": "qwen3.5-122b",
  "model_aliases": {
    "qwen": "/models/Qwen3.5-122B-A10B-int4-AutoRound"
  },
  "provider": {
    "base_url": "http://localhost:8000/v1",
    "api_key_env": "LLM_API_KEY",
    "timeout": 120
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
  "pricing": {
    "qwen3.5-122b": [0.50, 1.00],
    "default": [0, 0]
  },
  "vision": {
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
  }
}
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show commands |
| `/skill` | Browse & install skills |
| `/plugin` | Browse & install plugins |
| `/mcp` | Browse & install MCP servers |
| `/model <name>` | Switch model |
| `/memory` | Project memory |
| `/undo` | Undo last file change |
| `/cost` | Token usage + cost |
| `/budget <n>` | Set token budget |
| `/clear` | Clear conversation |
| `/session save` | Save session |
| `/index` | Project index |
| `/cd <dir>` | Change directory |
| `/exit` | Quit |

## Architecture

```
llm_code/
├── api/            # Provider abstraction (OpenAI-compat + Anthropic)
├── tools/          # 20+ builtin tools + agent + parsing
├── runtime/        # Conversation engine, permissions, hooks, session, memory
├── mcp/            # MCP client (stdio/HTTP/SSE/WebSocket) + OAuth
├── marketplace/    # Plugin system, 5 registries, ClawHub integration
├── lsp/            # LSP client, auto-detector, 3 tools
├── remote/         # WebSocket server/client + SSH proxy
├── cli/            # Print-based CLI + Ink bridge
ink-ui/             # React+Ink frontend (TypeScript)
```

### Layer Dependencies

```
cli → runtime → {tools, api}
         ↓
    mcp / lsp / marketplace / remote
```

## Development

```bash
git clone https://github.com/adamhong/llm-code
cd llm-code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

# Ink frontend
cd ink-ui && npm install
```

## Requirements

- Python 3.11+
- Node.js 18+ (for Ink UI; `--lite` mode works without it)
- An LLM server (vLLM, Ollama, LM Studio, or cloud API)

## License

MIT

# llm-code

<p align="center">
  <strong>Open-source CLI coding agent for any LLM</strong><br>
  Claude Code-level developer experience — with the model of your choice
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#features">Features</a> ·
  <a href="#marketplace">Marketplace</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#architecture">Architecture</a>
</p>

---

## What is llm-code?

A production-grade terminal coding agent that works with **any LLM** — local models (Qwen, Llama, Mistral via vLLM/Ollama) or cloud APIs (OpenAI, Anthropic, xAI, DeepSeek). Free or paid, your choice.

```
  +------------------+  +--------------------------------------+
  |                  |  | Local LLM Agent                      |
  |    LLM  CODE     |  | ------------------------------------ |
  |                  |  | Model        qwen3.5-122b            |
  +------------------+  | Workspace    my-project, main        |
                        | Directory    ~/my-project            |
                        | Permissions  prompt                  |
                        |                                      |
                        | Quick start  /help, /skill, /mcp     |
                        | Multiline    Shift+Enter             |
                        | Images       Cmd+V pastes            |
                        | ------------------------------------ |
                        | Ready                                |
                        +--------------------------------------+
```

## Features

### Any Model, One Tool

- **Multi-provider** — OpenAI-compatible servers (vLLM, Ollama, LM Studio), Anthropic, xAI, DeepSeek
- **Zero cost with local models** — run Qwen, Llama, or any open-weight model on your own hardware
- **Cloud APIs** — switch to GPT-4o, Claude, or Grok with a config change
- **Model aliases** — short names like `qwen`, `gpt`, `opus` resolve automatically
- **Model routing** — use different models for sub-agents and compaction

### Rich Terminal UI

- **React+Ink interface** — interactive menus, syntax highlighting, real-time streaming
- **Image support** — paste screenshots from clipboard (Cmd+V), attach local images
- **File operations** — read, write, edit, glob, grep with smart permissions
- **Tool panels** — see what the agent is doing in real-time
- **Lightweight mode** — `--lite` for a print-based CLI (no Node.js required)

### Agent Capabilities

- **Built-in tools** — file I/O, bash, glob, grep, git, LSP, memory, and more
- **Dual-track tool calling** — native function calling when available, XML fallback for any model
- **Sub-agents** — parallel child agents with specialized roles (Explore, Plan, Verify)
- **Context compression** — 4-level progressive compaction keeps long sessions efficient
- **Token budget** — `--budget` to control token spending per session
- **Cost tracking** — per-model pricing with custom config

### Smart Safety

- **Input-aware permissions** — `bash ls` auto-approved, `rm -rf` needs confirmation
- **Permission modes** — read_only, workspace_write, full_access, prompt, auto_accept
- **Hook system** — pre/post tool-use hooks for auto-formatting, linting, validation
- **Git checkpoint** — auto-checkpoint before writes, `/undo` to restore

### Remote Execution

- **Server mode** — `llm-code --serve` exposes a WebSocket JSON-RPC endpoint
- **Client mode** — `llm-code --connect host:port` to use a remote agent
- **SSH proxy** — `llm-code --ssh user@host` auto-tunnels and connects

## Marketplace

llm-code is compatible with Claude Code's plugin ecosystem — skills, plugins, and MCP servers work out of the box.

### Skills — `/skill`

Browse and install skills with an interactive selector:

```
 > brainstorming          (installed)
   test-driven-development (installed)
   code-review-fix         [ClawHub]
   security-check          [npm]
```

Sources: **ClawHub.ai** (largest AI skill marketplace), **npm** (official packages), **local plugins**

### Plugins — `/plugin`

Plugins bundle skills, hooks, MCP servers, and agents:

```bash
/plugin install obra/superpowers    # workflow skills
/plugin                             # browse marketplace
```

Sources: **Official** (Claude Code plugins), **ClawHub** (community), **npm**, **GitHub**

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

Supports **stdio**, **HTTP**, **SSE**, and **WebSocket** transports.

## Quick Start

```bash
# Install
pip install llm-code

# Configure (local model example)
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

### Modes

```bash
llm-code                       # Default: React+Ink UI
llm-code --lite                # Lightweight print-based CLI
llm-code --serve --port 8765   # Remote server
llm-code --connect host:8765   # Remote client
llm-code --ssh user@host       # SSH tunnel + connect
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
    "qwen": "/models/Qwen3.5-122B-A10B-int4-AutoRound",
    "fast": "qwen3.5-7b",
    "gpt": "gpt-4o"
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
  "hooks": [
    {"event": "post_tool_use", "tool_pattern": "write_file|edit_file", "command": "ruff format {path}"}
  ],
  "mcpServers": {}
}
```

### Slash Commands

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
├── tools/          # Builtin tools + agent + parsing
├── runtime/        # Conversation engine, permissions, hooks, session, memory
├── mcp/            # MCP client (stdio/HTTP/SSE/WebSocket) + OAuth
├── marketplace/    # Plugin system, registries, ClawHub integration
├── lsp/            # LSP client, auto-detector
├── remote/         # WebSocket server/client + SSH proxy
├── cli/            # Print-based CLI + Ink bridge
ink-ui/             # React+Ink frontend (TypeScript)
```

```
cli → runtime → {tools, api}
         ↓
    mcp / lsp / marketplace / remote
```

## Development

```bash
git clone https://github.com/djfeu-adam/llm-code
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

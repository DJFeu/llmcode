# llm-code

<p align="center">
  <strong>Open-source CLI coding agent for any LLM</strong><br>
  Production-grade agent runtime with Claude Code-level architecture — your model, your hardware
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

Not just a CLI tool — a full **AI Agent Runtime** with ReAct loop engine, multi-layer memory, speculative execution, 7-layer error recovery, and multi-agent orchestration.

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
- **Model routing** — different models for sub-agents, compaction, and fallback

### Agent Runtime Engine

- **ReAct 5-stage loop** — context prep, streaming, tool exec, attachment collection, continue/stop
- **Speculative execution** — copy-on-write overlay pre-executes writes before user confirms
- **7-layer error recovery** — API retry, 529 overload, tool fallback, reactive compact, token auto-upgrade, context drain, model fallback
- **4-level context compression** — snip, microcompact, autocompact, reactive (progressive)
- **Prompt cache splitting** — static/dynamic separation maximizes cache hits
- **Tool deferred loading** — core tools always visible, others discoverable via `tool_search` when >20
- **HIDA dynamic context** — classifies task type (10 categories), loads only relevant tools/memory/rules

### Rich Terminal UI

- **React+Ink interface** — interactive menus, syntax highlighting, real-time streaming
- **Image support** — paste screenshots from clipboard (Cmd+V), attach local images
- **Vim mode** — full vim keybindings (motions, operators, text objects) via `/vim`
- **Diff visualization** — syntax-highlighted diffs on every file change
- **Search highlight** — `/search` or Ctrl+F to find text across conversation history
- **Clickable links** — URLs auto-detected and wrapped with OSC8 hyperlinks
- **Voice input** — hold-to-talk STT with Whisper/Google/Anthropic backends
- **Lightweight mode** — `--lite` for a print-based CLI (no Node.js required)

### Agent Capabilities

- **Built-in tools** — file I/O, bash, glob, grep, git, LSP, memory, notebook, and more
- **Dual-track tool calling** — native function calling when available, XML fallback for any model
- **Sub-agents** — parallel child agents with specialized roles (Explore, Plan, Verify)
- **Extended thinking** — adaptive/enabled/disabled modes with visual thinking panel
- **Notebook support** — read and edit Jupyter `.ipynb` files directly
- **Computer use** — screenshot, mouse click, keyboard type for GUI automation
- **Task lifecycle** — PLAN -> DO -> VERIFY -> CLOSE state machine with cross-session persistence
- **Token budget** — `--budget` to control token spending per session
- **Cost tracking** — per-model pricing with custom config

### Multi-Agent Collaboration

- **Team/Swarm** — spawn multiple agents in parallel via `/swarm create <role> <task>`
- **Coordinator** — auto-decompose complex tasks and dispatch to workers via `/swarm coordinate`
- **tmux integration** — each agent runs in its own tmux pane (subprocess fallback)
- **Mailbox system** — agents communicate via file-based message passing
- **Shared memory** — all agents read/write the same project memory with file locking
- **Built-in roles** — coder, reviewer, researcher, tester (or custom)

### Defense-in-Depth Security

- **Bash 20-point security** — injection, newline attack, interpreter blacklist, credential protection, and more
- **File protection** — sensitive files (.env, SSH keys, credentials) blocked or warned on read/write
- **Sandbox detection** — Docker/container awareness with path restrictions
- **Input-aware permissions** — `bash ls` auto-approved, `rm -rf` needs confirmation
- **Permission modes** — read_only, workspace_write, full_access, prompt, auto_accept
- **Hook system** — 6 types, 24 events with glob matching and per-hook timeout/error handling
- **Git checkpoint** — auto-checkpoint before writes, `/undo` to restore

### Memory & Sessions

- **5-layer memory** — L0 governance (always loaded), L1 working, L2 project, L3 task, L4 summary
- **DreamTask** — auto-consolidates session knowledge on exit into long-term memory
- **Checkpoint recovery** — auto-save every 60s, `--resume` to restore full conversation state
- **Task lifecycle memory** — PLAN/DO/VERIFY/CLOSE states persist across sessions
- **VCR recording** — record structured event streams for debugging and replay
- **Cron scheduling** — schedule recurring agent tasks with standard cron expressions

### IDE Integration

- **Generic protocol** — WebSocket JSON-RPC server for any IDE to connect
- **Open in IDE** — agent can open files at specific lines in your editor
- **Diagnostics** — read lint/error info from connected IDE
- **Selection** — get currently selected code from IDE
- **Auto-detection** — detects running VSCode, JetBrains, Neovim, Sublime

### Observability

- **OpenTelemetry** — optional tracing for turns and tool executions
- **VCR replay** — `--replay` for offline event stream analysis
- **Cost tracking** — per-model pricing with session totals
- **Structured logging** — configurable log levels

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

### Optional Features

```bash
pip install llm-code[voice]          # Voice input (sounddevice)
pip install llm-code[computer-use]   # GUI automation (pyautogui, Pillow)
pip install llm-code[ide]            # IDE integration (psutil)
pip install llm-code[telemetry]      # OpenTelemetry tracing
```

### Modes

```bash
llm-code                       # Default: React+Ink UI
llm-code --lite                # Lightweight print-based CLI
llm-code --serve --port 8765   # Remote server
llm-code --connect host:8765   # Remote client
llm-code --ssh user@host       # SSH tunnel + connect
llm-code --replay <file>       # Replay a VCR recording
llm-code --resume              # Resume from last checkpoint
```

## Configuration

### Config Locations (precedence low -> high)

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
    "compaction": "qwen3.5-7b",
    "fallback": "qwen3.5-7b"
  },
  "thinking": { "mode": "adaptive", "budget_tokens": 10000 },
  "vim_mode": false,
  "voice": {
    "enabled": false,
    "backend": "whisper",
    "whisper_url": "http://localhost:8000/v1/audio/transcriptions",
    "language": "auto"
  },
  "computer_use": { "enabled": false },
  "ide": { "enabled": false, "port": 9876 },
  "swarm": { "enabled": false, "backend": "auto", "max_members": 5 },
  "dream": { "enabled": true, "min_turns": 3 },
  "vcr": { "enabled": false, "auto_record": false },
  "hida": { "enabled": true, "confidence_threshold": 0.5 },
  "telemetry": { "enabled": false, "endpoint": "http://localhost:4318" },
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
| `/memory consolidate` | Consolidate session into long-term memory |
| `/memory history` | View consolidation history |
| `/undo` | Undo last file change |
| `/cost` | Token usage + cost |
| `/budget <n>` | Set token budget |
| `/thinking` | Toggle thinking mode (adaptive/on/off) |
| `/vim` | Toggle vim mode |
| `/voice` | Toggle voice input |
| `/search <query>` | Search conversation history |
| `/task` | Task lifecycle management |
| `/swarm` | List/create/stop/coordinate agent swarm |
| `/cron` | List/add/delete scheduled tasks |
| `/vcr` | Start/stop/list session recordings |
| `/checkpoint` | Save/list/resume session checkpoints |
| `/ide` | IDE connection status |
| `/hida` | Show HIDA task classification |
| `/clear` | Clear conversation |
| `/session save` | Save session |
| `/index` | Project index |
| `/cd <dir>` | Change directory |
| `/exit` | Quit |

## Architecture

```
llm_code/
├── api/            # Provider abstraction (OpenAI-compat + Anthropic)
├── tools/          # Builtin tools + agent + deferred loading + parsing
├── runtime/        # ReAct engine, permissions, hooks, compression, memory layers,
│                   # dream, VCR, checkpoint, speculative exec, telemetry, HIDA
├── task/           # Task lifecycle (PLAN/DO/VERIFY/CLOSE state machine)
├── mcp/            # MCP client (stdio/HTTP/SSE/WebSocket) + OAuth
├── marketplace/    # Plugin system, registries, ClawHub integration
├── lsp/            # LSP client, auto-detector
├── remote/         # WebSocket server/client + SSH proxy
├── vim/            # Vim engine (motions, operators, text objects, state machine)
├── voice/          # Voice input (STT multi-backend: Whisper, Google, Anthropic)
├── computer_use/   # GUI automation (screenshot, mouse, keyboard)
├── cron/           # Task scheduling (cron parser, storage, async scheduler)
├── ide/            # IDE integration (WebSocket server, bridge, detector)
├── hida/           # Dynamic context loading (classifier, profiles, engine)
├── swarm/          # Multi-agent (manager, coordinator, backends, mailbox)
├── utils/          # Notebook parser, diff engine, hyperlinks, search
├── cli/            # Print-based CLI + Ink bridge
ink-ui/             # React+Ink frontend (TypeScript)
```

### Engine Layers

```
cli -> runtime (ReAct loop + 7-layer recovery + speculative exec)
          |
       tools (20-point bash security + file protection + deferred loading)
          |
       api (prompt cache split + streaming + model fallback)
          |
    mcp / lsp / marketplace / remote
          |
    memory (5 layers) / task lifecycle / HIDA / telemetry
          |
    vim / voice / computer_use / cron / ide / swarm + coordinator
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

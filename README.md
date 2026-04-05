# llm-code

<p align="center">
  <strong>Open-source AI agent runtime for any LLM</strong><br>
  Production-grade coding agent with Claude Code-level architecture — your model, your hardware, zero vendor lock-in
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-llm-code">Why llm-code</a> ·
  <a href="#features">Features</a> ·
  <a href="#marketplace">Marketplace</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-2861%20passing-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

---

## Why llm-code?

Most AI coding tools lock you into a single provider. **llm-code doesn't.**

Run the same agent experience with a free local model on your own GPU, or with any cloud API. Switch between them with one config change. No API key required for local models.

```
 ██╗      ██╗      ███╗   ███╗
 ██║      ██║      ████╗ ████║
 ██║      ██║      ██╔████╔██║
 ██║      ██║      ██║╚██╔╝██║
 ███████╗ ███████╗ ██║ ╚═╝ ██║
 ╚══════╝ ╚══════╝ ╚═╝     ╚═╝
  ██████╗  ██████╗  ██████╗  ███████╗
 ██╔════╝ ██╔═══██╗ ██╔══██╗ ██╔════╝
 ██║      ██║   ██║ ██║  ██║ █████╗
 ██║      ██║   ██║ ██║  ██║ ██╔══╝
 ╚██████╗ ╚██████╔╝ ██████╔╝ ███████╗
  ╚═════╝  ╚═════╝  ╚═════╝  ╚══════╝
```

**Not just a CLI tool** — a complete AI Agent Runtime with:

- **ReAct engine** with 5-stage turn loop and streaming tool execution
- **7-layer error recovery** that self-heals instead of crashing
- **5-layer memory system** with governance, working, project, task, and summary memory
- **Multi-agent orchestration** with coordinator pattern and inter-agent messaging
- **Defense-in-depth security** with 21-point bash checks and sensitive file protection

## Quick Start

```bash
pip install llm-code
```

**With a local model (zero cost):**

```bash
mkdir -p ~/.llm-code
cat > ~/.llm-code/config.json << 'EOF'
{
  "model": "qwen3.5",
  "provider": {
    "base_url": "http://localhost:8000/v1"
  }
}
EOF

llm-code
```

**With a cloud API:**

```bash
cat > ~/.llm-code/config.json << 'EOF'
{
  "model": "gpt-4o",
  "provider": {
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY"
  }
}
EOF

llm-code
```

### Modes

```bash
llm-code                       # Default: Fullscreen TUI (Python Textual)
llm-code --provider ollama     # Auto-detect Ollama + interactive model selector
llm-code --serve --port 8765   # Remote WebSocket server
llm-code --connect host:8765   # Connect to remote agent
llm-code --ssh user@host       # SSH tunnel + auto-connect
llm-code --replay <file>       # Replay a recorded session
llm-code --resume              # Resume from checkpoint
```

### Optional Features

```bash
pip install llm-code[voice]          # Voice input via STT
pip install llm-code[computer-use]   # GUI automation
pip install llm-code[ide]            # IDE integration
pip install llm-code[telemetry]      # OpenTelemetry tracing
```

---

## Features

### Model Freedom

| Provider | Examples | Cost |
|----------|----------|------|
| **Local (vLLM)** | Qwen 3.5, Llama, Mistral, DeepSeek | Free |
| **Local (Ollama)** | Any GGUF model | Free |
| **Local (LM Studio)** | Any supported model | Free |
| **OpenAI** | GPT-4o, GPT-4o-mini, o3 | Pay-per-use |
| **Anthropic** | Claude Opus, Sonnet, Haiku | Pay-per-use |
| **Google** | Gemini 2.5 Pro, Gemini 2.5 Flash | Pay-per-use |
| **xAI** | Grok | Pay-per-use |
| **DeepSeek** | DeepSeek V3, R1 | Pay-per-use |

- **Model aliases** — `qwen`, `gpt`, `opus`, `sonnet` resolve to full model paths
- **Model routing** — different models for sub-agents, compaction, and fallback
- **Local models get unlimited token output** — no artificial cap on localhost

### Agent Runtime Engine

The core loop follows a 5-stage **ReAct** (Reason + Act) pattern:

1. **Context preparation** — compress history, load relevant memory, apply HIDA filtering
2. **Streaming model call** — send conversation + tools, stream response in real-time
3. **Tool execution** — read-only tools run concurrently during streaming; writes wait
4. **Attachment collection** — gather file changes, task state, memory updates
5. **Continue or stop** — loop back if tools were called, stop if model is done

**Resilience features:**

- **7-layer error recovery** — API retry with exponential backoff, 529 overload handling (30/60/120s), native-to-XML tool fallback, reactive context compression, token limit auto-upgrade, context drain, model fallback after 3 consecutive failures
- **Speculative execution** — writes pre-execute in a tmpdir overlay before user confirms; confirm copies back, deny discards
- **4-level context compression** — snip (truncate tool results), microcompact (deduplicate reads), autocompact (AI summary), reactive (emergency on 413)
- **Cache-aware compression** — preferentially removes non-API-cached messages to preserve cache hits
- **3-tier prompt cache** — global/project/session scope boundaries for optimal API cache utilization
- **HIDA dynamic loading** — classifies input into 10 task types, loads only relevant tools/memory/governance rules

### Tools

Built-in tools with smart permission classification:

| Category | Tools |
|----------|-------|
| **File I/O** | read_file, write_file, edit_file (with fuzzy quote matching + mtime conflict detection) |
| **Search** | glob_search, grep_search, tool_search (deferred tool discovery) |
| **Execution** | bash (21-point security), agent (sub-agents) |
| **Git** | git_status, git_diff, git_log, git_commit, git_push, git_stash, git_branch |
| **Notebook** | notebook_read, notebook_edit (Jupyter .ipynb) |
| **Computer Use** | screenshot, mouse_click, keyboard_type, key_press, scroll, mouse_drag |
| **Task Lifecycle** | task_plan, task_verify, task_close |
| **Scheduling** | cron_create, cron_list, cron_delete |
| **IDE** | ide_open, ide_diagnostics, ide_selection |
| **Swarm** | swarm_create, swarm_list, swarm_message, swarm_delete, coordinate |
| **Memory** | LSP, memory tools |

When tool count exceeds 20, non-core tools are deferred and discoverable via `tool_search`.

### Multi-Agent Collaboration

```bash
/swarm create coder "Implement the login API"
/swarm create tester "Write tests for the login API"
/swarm create reviewer "Review the login implementation"
/swarm coordinate "Build a complete user auth system"
```

- **Coordinator** auto-decomposes complex tasks into subtasks and dispatches to workers
- **tmux backend** — each agent in its own terminal pane (subprocess fallback for non-tmux)
- **Mailbox** — file-based JSONL message passing between agents
- **Shared memory** — all agents access the same project memory with file locking
- **Built-in roles** — `coder`, `reviewer`, `researcher`, `tester`, or define custom roles

### Security

**21-point Bash security:**

Injection detection, newline attack prevention, pipe chain limits, interpreter REPL blacklist, environment variable leak protection, network access control, file permission change detection, system package operation alerts, redirect overwrite detection, credential path protection, background execution detection, recursive operation warnings, multi-command chain limits, and Zsh dangerous builtin blocking.

**File protection:** Sensitive files (`.env`, SSH keys, `credentials.*`, `*.pem`) are blocked on write and warned on read.

**Sandbox detection:** Auto-detects Docker/container environments and restricts paths.

**Permission system:** 5 modes (read_only → auto_accept) with allow/deny lists, shadowed rule detection, and input-aware classification (`ls` auto-approved, `rm -rf` needs confirmation).

### Memory System

| Layer | Scope | Lifetime | Purpose |
|-------|-------|----------|---------|
| **L0 Governance** | Project | Permanent | Rules from CLAUDE.md + .llm-code/rules/ — always loaded |
| **L1 Working** | Session | Ephemeral | In-memory scratch space for current task |
| **L2 Project** | Project | Long-term | DreamTask-consolidated knowledge with tag-based queries |
| **L3 Task** | Cross-session | Until done | PLAN/DO/VERIFY/CLOSE state machine persisted as JSON |
| **L4 Summary** | Per-session | Long-term | Conversation summaries for future reference |

**DreamTask:** On session exit, automatically consolidates conversation into structured long-term memory — files modified, decisions made, patterns learned.

**Checkpoint recovery:** Auto-saves every 60 seconds. Resume with `--resume` or `/checkpoint resume`.

### Task Lifecycle

```
PLAN --> DO --> VERIFY --> CLOSE --> DONE
                  |
           [auto checks]
            pass --> CLOSE
            fail --> diagnostics
                     |-- continue (minor fix)
                     |-- replan (redo PLAN)
                     |-- escalate (ask user)
```

- **VERIFY** runs automated checks: pytest, ruff, file existence — then LLM judges
- **Cross-session:** incomplete tasks persist and resume in the next session
- **CLOSE** writes summaries to L3 task memory and L2 project memory

### Terminal UI

- **Fullscreen TUI** (default) — Python Textual, no Node.js required, Claude Code-style UI
  - Welcome banner, markdown rendering, syntax-highlighted code blocks
  - Slash command autocomplete dropdown with `Tab`/arrow navigation
  - Inline `[image]` markers with `Cmd+V` paste support
  - Interactive marketplace browser for skills, plugins, and MCP servers
  - Tabbed `/help` modal (general / commands / custom-commands)
  - ToolBlock diff view with colored +/- lines and line numbers
  - Spinner with orange→red color transition on long operations
  - Permission prompts with single-key y/n/a
  - Cursor movement (←→, Home/End) in input bar
- **Vim mode** — full motions (hjkl, w/b/e, 0/$, gg/G, f/F/t/T), operators (d/c/y), text objects (iw, i", i()
- **Diff visualization** — colored inline diffs on every file change
- **Search** — `/search` or Ctrl+F with match highlighting
- **OSC8 hyperlinks** — clickable URLs in supporting terminals
- **Voice input** — hold-to-talk STT (Whisper, Google, Anthropic backends)
- **Extended thinking** — collapsible thinking panel with adaptive/enabled/disabled modes

### Hook System

6 event categories, 24 events, glob pattern matching:

| Category | Events |
|----------|--------|
| **tool** | pre_tool_use, post_tool_use, tool_error, tool_denied |
| **command** | pre_command, post_command, command_error |
| **prompt** | prompt_submit, prompt_compile, prompt_cache_hit, prompt_cache_miss |
| **agent** | agent_spawn, agent_complete, agent_error, agent_message |
| **session** | session_start, session_end, session_save, session_compact, session_dream |
| **http** | http_request, http_response, http_error, http_retry, http_fallback |

```json
{
  "hooks": [
    {"event": "post_tool_use", "tool_pattern": "write_file|edit_file", "command": "ruff format {path}"},
    {"event": "session.*", "command": "echo $HOOK_EVENT >> ~/agent.log", "on_error": "ignore"}
  ]
}
```

### IDE Integration

llm-code runs a WebSocket JSON-RPC server that any IDE can connect to:

- **Open files** at specific lines in your editor
- **Read diagnostics** (lint errors, type errors) from the IDE
- **Get selection** — the agent can read your currently selected code
- **Auto-detection** — scans for running VSCode, JetBrains, Neovim, Sublime

### Observability

- **OpenTelemetry** — spans for turns and tool executions with LLM semantic conventions
- **VCR recording** — structured JSONL event streams for debugging and replay
- **Cost tracking** — per-model pricing with cache-aware calculations and budget enforcement
- **Version check** — notifies on startup if a newer release is available

---

## Marketplace

Compatible with Claude Code's plugin ecosystem — skills, plugins, and MCP servers work out of the box.

### Skills — `/skill`

```
 > brainstorming          (installed)
   test-driven-development (installed)
   code-review-fix         [ClawHub]
   security-check          [npm]
```

Sources: **ClawHub.ai**, **npm**, **local plugins**

### Plugins — `/plugin`

```bash
/plugin install obra/superpowers
```

Sources: **Official** (Claude Code), **ClawHub**, **npm**, **GitHub**

### MCP Servers — `/mcp`

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

Supports **stdio**, **HTTP**, **SSE**, and **WebSocket** transports with health monitoring and auto-reconnection.

---

## Configuration

### Config Locations (precedence low -> high)

1. `~/.llm-code/config.json` — User global
2. `.llm-code/config.json` — Project
3. `.llm-code/config.local.json` — Local (gitignored)
4. CLI flags / env vars — Highest

### Example Config

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
    "allow_tools": ["read_file", "glob_search", "grep_search"]
  },
  "model_routing": {
    "sub_agent": "qwen3.5-32b",
    "compaction": "qwen3.5-7b",
    "fallback": "qwen3.5-7b"
  },
  "max_budget_usd": 5.00,
  "thinking": { "mode": "adaptive", "budget_tokens": 10000 },
  "dream": { "enabled": true, "min_turns": 3 },
  "hida": { "enabled": true },
  "hooks": [
    {"event": "post_tool_use", "tool_pattern": "write_*|edit_*", "command": "ruff format {path}"}
  ],
  "mcpServers": {}
}
```

### Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model <name>` | Switch model |
| `/config` | View/set runtime configuration |
| `/session` | Session management |
| `/skill` | Browse & install skills |
| `/plugin` | Browse & install plugins |
| `/mcp` | Browse & install MCP servers |
| `/memory` | View project memory |
| `/memory consolidate` | Run DreamTask now |
| `/memory history` | View consolidation history |
| `/task` | Task lifecycle (new/verify/close) |
| `/swarm` | Multi-agent (create/coordinate/stop) |
| `/search <query>` | Search conversation history |
| `/thinking` | Toggle thinking mode |
| `/vim` | Toggle vim keybindings |
| `/voice` | Toggle voice input |
| `/image` | Paste/load an image |
| `/cron` | Scheduled tasks |
| `/vcr` | Session recording |
| `/checkpoint` | Session checkpoints |
| `/ide` | IDE connection status |
| `/lsp` | Language Server Protocol status |
| `/index` | Codebase indexing |
| `/hida` | HIDA classification info |
| `/cd <path>` | Change working directory |
| `/undo` | Undo last file change |
| `/cancel` | Cancel running operation |
| `/cost` | Token usage + cost |
| `/budget <n>` | Set token budget |
| `/clear` | Clear conversation |
| `/exit`, `/quit` | Quit |

---

## Architecture

```
llm_code/               21,000 lines Python
├── api/                Provider abstraction (OpenAI-compat + Anthropic)
├── cli/                CLI entry point + Textual TUI launcher
├── runtime/            ReAct engine, memory layers, compression, hooks,
│                       permissions, checkpoint, dream, VCR, speculative
│                       execution, telemetry, file protection, sandbox
├── tools/              30+ tools with deferred loading + security
├── task/               PLAN/DO/VERIFY/CLOSE state machine
├── hida/               Dynamic context loading (10-type classifier)
├── mcp/                MCP client (4 transports) + OAuth + health checks
├── marketplace/        Plugin system + ClawHub integration
├── lsp/                Language Server Protocol client
├── remote/             WebSocket server/client + SSH proxy
├── vim/                Vim engine (motions, operators, text objects)
├── voice/              STT (Whisper, Google, Anthropic backends)
├── computer_use/       GUI automation (screenshot + input control)
├── cron/               Task scheduler (cron parser + async poller)
├── ide/                IDE bridge (WebSocket JSON-RPC server)
├── swarm/              Multi-agent (coordinator, tmux/subprocess, mailbox)
├── utils/              Notebook, diff, hyperlinks, search, text normalize
tests/                  2,861 tests across 170+ test files
```

---

## Contributing

```bash
git clone https://github.com/DJFeu/llm-code
cd llm-code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                  # 2,861 tests
ruff check llm_code/    # lint
```

### Requirements

- Python 3.11+
- An LLM server (vLLM, Ollama, LM Studio, or cloud API)

---

## License

MIT

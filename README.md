# llmcode

<p align="center">
  <img src="assets/banner.png" alt="LLM CODE" width="600">
</p>

<p align="center">
  <strong>Python-native coding agent runtime tuned for local LLMs</strong><br>
  6-stage agent permissions · fork-cache parallel agents · 5-layer memory · per-model prompts for Qwen / Llama / DeepSeek
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-llmcode">Why llmcode</a> ·
  <a href="#features">Features</a> ·
  <a href="#how-it-compares">vs Other Tools</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#docs">Docs</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-5160%20passing-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/cold%20start-~400ms-brightgreen" alt="Cold start">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/pypi/v/llmcode-cli" alt="PyPI">
</p>

---

## Why llmcode?

There are several great open-source AI coding agents now ([Claude Code](https://github.com/anthropics/claude-code), [opencode](https://github.com/anomalyco/opencode), [Aider](https://github.com/Aider-AI/aider), [Codex CLI](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), etc). llmcode exists for a specific niche they don't fully serve:

> **You want a Claude Code-style coding agent that runs your own model on your own GPU, written in Python so it integrates with your existing Python LLM stack, with deep optimization for the smaller models you'll actually run locally.**

If you check any of these boxes:

- You run **vLLM, Ollama, or LM Studio** with Qwen / Llama / DeepSeek locally
- You don't want **another Node.js runtime** in your stack (you already have Python)
- You've tried tools tuned for Claude/GPT and watched smaller models drown in the system prompt
- You need **multi-agent coordination that doesn't over-spawn** on local models
- You want **user-defined agents** via Markdown files — no code changes needed
- You want **parallel fork agents** with prompt-cache sharing (40-60% token savings on Anthropic)
- You want **persistent project memory** that survives across sessions
- You care about **CJK / multi-language** terminal handling

then llmcode is for you.

If you mostly use cloud APIs and don't need any of the above, **opencode is more mature** and you should probably use it.

## Quick Start

```bash
pip install llmcode-cli
```

**Upgrade:**

```bash
# From terminal
pip install --upgrade llmcode-cli

# Or from inside llmcode TUI
/update
```

llmcode checks for updates automatically on startup (background, cached 6 hours). When a new version is available you'll see:

```
Update available: 1.17.0 → 1.18.0 (run /update)
```

> **`llmcode: command not found`?** pip installs scripts to `~/.local/bin` (Linux/macOS) or `%APPDATA%\Python\Scripts` (Windows). Add it to your PATH:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

**With a local model (zero cost, fully offline):**

```bash
mkdir -p ~/.llmcode
cat > ~/.llmcode/config.json << 'EOF'
{
  "model": "qwen3.5",
  "provider": {
    "base_url": "http://localhost:8000/v1"
  }
}
EOF

llmcode
```

**With a cloud API:**

```bash
cat > ~/.llmcode/config.json << 'EOF'
{
  "model": "claude-sonnet-4-6",
  "provider": {
    "base_url": "https://api.anthropic.com/v1",
    "api_key_env": "ANTHROPIC_API_KEY"
  }
}
EOF

llmcode
```

**Docker (self-hosted):**

```bash
docker pull ghcr.io/djfeu/llmcode:latest
docker run -it --rm \
  -v "$PWD:/workspace" \
  -v "$HOME/.llmcode:/home/llmcode/.llmcode" \
  --network host \
  ghcr.io/djfeu/llmcode
```

### Modes

```bash
llmcode                       # Default fullscreen TUI
llmcode --provider ollama     # Auto-detect Ollama + interactive model selector
llmcode --mode plan           # Read-only mode, plan before execution
llmcode --yolo                # Auto-accept all permissions (dangerous)
llmcode -x "find large files" # Shell assistant: translate to command + execute
llmcode -q "explain this"     # Quick Q&A without TUI
llmcode --serve --port 8765   # Remote WebSocket server
llmcode --connect host:8765   # Connect to remote agent
llmcode --resume              # Resume from checkpoint
```

---

## How it compares

llmcode is **deeply influenced by Claude Code's architecture**, borrows proven patterns from [Codex CLI](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [opencode](https://github.com/anomalyco/opencode), and [Qwen Code](https://github.com/QwenLM/qwen-code).

| Feature | llmcode | Claude Code | Codex CLI | Gemini CLI | Qwen Code | opencode |
|---------|:-------:|:-----------:|:---------:|:----------:|:---------:|:--------:|
| Open source | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Language | Python | TypeScript | Rust+TS | TypeScript | TypeScript | TypeScript |
| Local model first | ✅ | ❌ | ❌ | ❌ | ✅ | ⚠️ |
| Default model | any | Claude | GPT/o-series | Gemini | Qwen3-Coder | any |
| Free tier | self-hosted | ❌ | ❌ | ✅ | 1000 req/day | self-hosted |
| Per-model system prompts | ✅ | N/A | ❌ | ❌ | ⚠️ | ✅ |
| **Qwen/Llama/DeepSeek tuned** | ✅ | ❌ | ❌ | ❌ | ⚠️ | ❌ |
| Model profile system (TOML) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Skill router (auto match) | **3-tier** | ❌ | ❌ | manual | manual | manual |
| Memory system | **5-layer** | basic | ❌ | basic | basic | basic |
| **Agent permission model** | **6-stage** | 6-stage | trait-based | policy-based | basic | basic |
| **User-defined agents (.md)** | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
| **Fork agents + cache sharing** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Agent memory persistence** | **3-scope** | 3-scope | ❌ | ❌ | ❌ | ❌ |
| **Git worktree isolation** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Exec policy rules (.rules)** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **Sandbox denial learning** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Per-turn tool visibility** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **Tool desc distillation** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Snippet-composable prompt** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Skill extraction** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Approval session cache** | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| Multi-agent coordinator | **synthesis** | ❌ | ❌ | ❌ | Arena | task tool |
| Specialist personas | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠️ |
| Plan mode | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ |
| Docker sandbox | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| PTY (interactive shell) | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Prompt caching (Anthropic) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Signed thinking round-trip | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Extension/plugin system | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Theme system | **8** | ❌ | ❌ | **15+** | ❌ | ❌ |
| IDE extensions | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| MCP servers | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Voice input | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Computer use | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Notebook tools | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| YOLO mode | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Where each tool shines

**llmcode** — Python-native, local-model-first. Aggregates the best patterns from every competitor: 6-stage agent permissions + fork cache (Claude Code), execution policy rules + approval cache (Codex), denial learning + snippet prompts + skill extraction (Gemini CLI). Plus unique: 5-layer memory, synthesis-first multi-agent, per-model prompt tuning for 9 families, model profiles (TOML).

**Claude Code** — Most polished UX, deepest Claude integration, closed-source and cloud-only.

**Codex CLI** — Rust core for performance, trait-based sandbox with Guardian subagent for risk assessment. OpenAI-only.

**Gemini CLI** — 15+ themes, extension manifest with hot-swapping, denial learning, skill extraction. Google-only.

**Qwen Code** — Best for Qwen models: free 1000 req/day, IDE extensions, messaging deployment (Telegram/WeChat/DingTalk), full i18n. Based on Gemini CLI.

**opencode** — Wider community, more mature, TypeScript ecosystem native.

---

## Features

### Local-LLM optimization

This is llmcode's core focus. Local models behave very differently from Claude / GPT:

- **They drown in big system prompts.** llmcode's 3-tier skill router only injects skills that match the current intent — keyword match → TF-IDF similarity → optional LLM classifier. No more "all 28 skills loaded every turn".
- **They follow instructions too literally.** llmcode has separate per-model system prompts for Qwen, Llama, DeepSeek, Kimi, Codex, Gemini, GPT, and Claude — auto-selected from model name.
- **They tend to repeat themselves.** llmcode's diminishing returns detection auto-stops when continuation produces < 500 new tokens for 3+ iterations in a row.
- **They over-spawn agents.** llmcode's coordinator forces a synthesis step before delegation, asking "should I delegate at all?" before splitting work.

### Memory system (5 layers)

| Layer | Purpose | Lifetime |
|-------|---------|----------|
| **L0 Governance** | Project rules from `CLAUDE.md` / `AGENTS.md` / `.llmcode/governance.md` | Permanent, always loaded |
| **L1 Working** | Current task scratch space | Ephemeral |
| **L2 Project** | Long-term project knowledge with 4-type taxonomy (user/feedback/project/reference) | Persistent, DreamTask consolidates |
| **L3 Task** | Multi-session task state machine (PLAN→DO→VERIFY→CLOSE→DONE) | Cross-session |
| **L4 Summary** | Past session summaries | Persistent |

Plus typed memory with `MEMORY.md` index, 25KB hard limit, and content validation that rejects derivable content (git logs, code dumps, file path lists).

See [docs/memory.md](docs/memory.md) for the full guide.

### Agent System (claude-code inspired)

Architecture borrowed from claude-code's sourcemap — 6-phase design for production-grade agent orchestration:

**6-stage tool permission model** — MCP bypass → global deny → custom agent deny → async allow-list → teammate extras → coordinator mode. Pure function, no global state. Built-in agents keep the `agent` tool (depth-guarded); user-defined agents have it blocked at Stage 4.

**Parallel fork with cache sharing** — `fork_directives` spawns N children in parallel. All children share a byte-identical API request prefix (system prompt + history + placeholder tool_results), so Anthropic's prompt cache is hit for children 2–N. Provider-agnostic: other providers work correctly without cache savings. Recursion guard via `<fork-boilerplate>` tag detection.

**User-defined agents** — Drop a `.md` file in `~/.llm-code/agents/` or `.llm-code/agents/`:

```yaml
---
name: security-auditor
description: Security-focused code reviewer
tools:
  - read_file
  - grep_search
  - bash
disallowed_tools:
  - write_file
---

You are a security auditor. Analyze code for OWASP Top 10...
```

Cascade: built-in → user-global → project-local (later shadows earlier).

**3-scope agent memory** — Agents can persist learnings across sessions:

| Scope | Path | Lifetime |
|-------|------|----------|
| user | `~/.llm-code/agent-memory/<agent>/` | Cross-project |
| project | `.llm-code/agent-memory/<agent>/` | In VCS |
| local | `.llm-code/agent-memory-local/<agent>/` | Gitignored |

**contextvars isolation** — Python `contextvars.ContextVar` prevents concurrent background agents from cross-contaminating telemetry and state (equivalent to claude-code's `AsyncLocalStorage`).

**Git worktree isolation** — Agents with `isolation: worktree` run in a `git worktree add` copy. Dirty worktrees are preserved with path+branch returned to the parent; clean ones are auto-removed.

### Coordinator with synthesis-first

```
user task → synthesize → should_delegate? → decompose → spawn/resume → wait → aggregate
```

The coordinator's first action is **not** decomposition — it's a synthesis check that asks the LLM "do I actually need to delegate this, and if so, what do I already know vs. what needs investigation?" This catches 30-50% of cases where naive coordinators would have spawned 3-5 unnecessary workers for trivial tasks.

Plus subagent resume — pass `resume_member_ids` to continue existing workers instead of spawning fresh, so multi-stage workflows keep their accumulated context.

See [docs/coordinator.md](docs/coordinator.md) for the full tutorial.

### Tools

| Category | Tools |
|----------|-------|
| **File I/O** | read_file, write_file, edit_file, multi_edit (with resolve_path workspace boundary check) |
| **Search** | glob_search, grep_search, tool_search |
| **Web** | web_search (DuckDuckGo / Brave / Tavily / SearXNG backends), web_fetch |
| **Execution** | bash (21-point security + Docker sandbox + PTY mode), agent (sub-agents with tier-based role routing: build / plan / explore / verify / general), enter_plan_mode, exit_plan_mode |
| **LSP** | lsp_hover, lsp_document_symbol, lsp_workspace_symbol, lsp_go_to_definition, lsp_find_references, lsp_go_to_implementation, lsp_call_hierarchy, lsp_diagnostics (auto-detects 25+ language servers via walk-up root finder) |
| **Git** | git_status, git_diff, git_log, git_commit, git_push, git_stash, git_branch |
| **Notebook** | notebook_read, notebook_edit |
| **Computer Use** | screenshot, mouse_click, keyboard_type, key_press, scroll, mouse_drag |
| **Task Lifecycle** | task_plan, task_verify, task_close |
| **Scheduling** | cron_create, cron_list, cron_delete |
| **IDE** | ide_open, ide_diagnostics, ide_selection |
| **Swarm** | swarm_create, swarm_list, swarm_message, swarm_delete, coordinate |
| **Skills** | skill_load (LLM-driven loading on top of auto-router) |

**Smart per-model tool selection**: GPT models get `apply_patch` (unified diff format), other models get `edit_file`. Auto-detected from model name.

**Path resolution**: `resolve_path()` auto-corrects wrong absolute paths from LLM (e.g. `llm-code` vs `llm_code` confusion) with workspace boundary check to prevent path traversal.

### Model Profile System

Declarative per-model profiles replace scattered hardcoded model adaptations. Profiles control:

- **Provider capabilities** — native tools, image support, reasoning mode
- **Streaming behavior** — implicit thinking, reasoning field names, thinking budget format (`chat_template_kwargs` vs `anthropic_native`)
- **Deployment** — local model detection (unlimited token upgrades), auto-discovery via `/v1/models` probe
- **Routing** — per-model tier-C skill router model override
- **Pricing** — per-1M-token input/output costs for cost tracking

Built-in profiles for Qwen3/3.5, Claude, GPT-4o, DeepSeek-R1, o3/o4-mini. User overrides via `~/.llmcode/model_profiles/*.toml`. See [`examples/model_profiles/`](examples/model_profiles/) for templates.

### Anthropic Provider

Native httpx-based provider for Anthropic's Messages API:

- **Prompt caching** — automatic `cache_control` on system prompt, tools, and last user message
- **Signed thinking** — signature delta accumulation for extended thinking round-trip
- **Server tool use** — `server_tool_use` / `server_tool_result` blocks with signature round-trip (web search, etc.)
- **Overload backoff** — progressive 30s → 60s → 120s retry on 529

### Security

- **21-point bash security** — injection detection, network access control, credential paths, recursive operation warnings, etc.
- **MCP instruction sanitization** — strips prompt injection patterns
- **Bash output secret scanning** — auto-redacts AWS/GitHub/JWT keys before they enter LLM context
- **Environment variable filtering** — sensitive vars replaced with `[FILTERED]`
- **File protection** — `.env`, SSH keys, `*.pem` blocked on write
- **Workspace boundary checks** — file tools refuse paths outside the project tree
- **Docker sandbox** — optional container isolation for bash commands (Docker/Podman auto-detected, configurable image/network/memory limits)
- **Plugin permissions gate** — blocks plugins requesting subprocess/fs_write/env unless `--force`

### Terminal UI

- **Mouse wheel scrolling** — `mouse=True` enables native scroll inside ChatScrollView; hold Option (macOS) or Shift (Linux) for text selection
- **Cmd+V auto-detect** — text via bracketed paste, image via clipboard fallback
- **Shift+Tab cycles agents** — BUILD → PLAN → SUGGEST → BUILD
- **PageUp/Down + Shift+↑/↓** — scrollback navigation
- **`/update`** — check PyPI + upgrade in-place (auto-check on startup, cached 6h)
- **`/theme <name>`** — switch color theme (default, dracula, monokai, tokyo-night, github-dark, solarized-dark, nord, gruvbox)
- **`/yolo`** — toggle auto-accept
- **`/init`** — generate `AGENTS.md` from repo analysis
- **`/copy`** — copy last response to clipboard
- **`/search`** — cross-session FTS5 search
- **`/personas`** — list specialist agents (Sisyphus refactor / Oracle deep-analysis / Atlas orchestrator / Librarian / Explore / Metis / Momus / Multimodal-Looker / WebResearcher)
- **`/orchestrate <task>`** — category-routed persona dispatch with retry-on-failure
- **`/profile`** — per-model token/cost breakdown for the current session
- **`/settings`** — settings panel
- **`/set <key> <value>`** — live config write-back (temperature, max_tokens, model)
- **`/model`** — switch model with profile info display (capabilities, pricing, provider)
- **`/export <path>`** — chunked markdown export of the conversation
- **`/compact`** — manually compact conversation history
- **Ctrl+P** — Quick Open fuzzy file finder
- **Click-to-open URLs** — markdown links and bare URLs in chat are clickable (cell-aware, CJK-safe)
- **180 spinner verbs** — Pondering, Caramelizing, Brewing… randomized per turn
- **Background task indicator** — status bar shows running/pending tasks
- **Vim mode** — full motions, operators, text objects

<details>
<summary><strong>Complete slash command reference (52)</strong></summary>

| Command | Description |
|---|---|
| `/help` | Show help |
| `/clear` | Clear conversation |
| `/model <name>` | Switch model |
| `/theme <name>` | Switch TUI color theme |
| `/cost` | Token usage |
| `/cache [list\|clear\|probe]` | Manage persistent caches |
| `/budget <tokens>` | Set token budget |
| `/undo` | Undo last change |
| `/cd <path>` | Change directory |
| `/config` | Runtime config |
| `/settings` | Open settings panel |
| `/set <key> <value>` | Set config value |
| `/thinking` | Toggle thinking |
| `/vim` | Toggle vim mode |
| `/image <path>` | Attach image |
| `/search <query>` | Search history |
| `/index` | Project index |
| `/session` | Sessions |
| `/skill` | Browse skills |
| `/plugin` | Browse plugins |
| `/mcp` | MCP servers |
| `/memory` | Project memory |
| `/cron` | Scheduled tasks |
| `/task` | Task lifecycle |
| `/swarm` | Swarm coordination |
| `/personas` | List built-in swarm personas |
| `/orchestrate <task>` | Category-routed persona dispatch |
| `/voice [on\|off]` | Voice input (STT) |
| `/ide` | IDE bridge |
| `/vcr` | VCR recording |
| `/checkpoint` | Checkpoints |
| `/diff` | Diff since checkpoint |
| `/hida` | HIDA task classification |
| `/lsp` | LSP status |
| `/cancel` | Cancel generation |
| `/plan` | Plan/Act mode |
| `/mode` | Switch mode (suggest/normal/plan) |
| `/analyze` | Code analysis |
| `/diff_check` | Diff analysis |
| `/dump` | Dump context |
| `/map` | Repo map |
| `/harness` | Harness controls |
| `/knowledge [rebuild]` | Knowledge base |
| `/gain` | Token savings report |
| `/profile` | Per-model token/cost breakdown |
| `/init` | Generate `AGENTS.md` from repo analysis |
| `/yolo` | Toggle YOLO mode |
| `/copy` | Copy last response to clipboard |
| `/compact [keep]` | Compact conversation |
| `/export [path]` | Export conversation to markdown |
| `/update` | Check PyPI + upgrade in-place |
| `/exit`, `/quit` | Quit |

Type `/` in the TUI for autocomplete with inline descriptions, or run `/help` for the interactive browser.

</details>

### Hooks (24 events)

```json
{
  "hooks": [
    {"event": "post_tool_use", "tool_pattern": "write_file|edit_file", "command": "ruff format {path}"},
    {"event": "session.*", "command": "echo $HOOK_EVENT >> ~/agent.log", "on_error": "ignore"}
  ]
}
```

Categories: tool, command, prompt, agent, session, http.

**Builtin hooks** (opt-in via `config.builtin_hooks.enabled`):
- `context_window_monitor` — warns once per session when input tokens exceed 75% of the model's context limit
- `thinking_mode` — detects "ultrathink" / 深入思考 keywords in user prompts and boosts the next turn's thinking budget
- `rules_injector` — auto-injects `CLAUDE.md` / `AGENTS.md` / `.cursorrules` content when reading files inside a project that has them
- `auto_format` — format files after write/edit (existing)

### Marketplace

Compatible with Claude Code's plugin ecosystem.

```bash
/skill                       # Browse skills
/plugin install obra/superpowers
/mcp                         # Browse MCP servers
```

Sources: Official (`anthropics/claude-plugins-official`), Community, npm, GitHub.

---

## Configuration

```json
{
  "model": "qwen3.5",
  "provider": {
    "base_url": "http://localhost:8000/v1",
    "timeout": 120
  },
  "permissions": {
    "mode": "prompt"
  },
  "model_routing": {
    "sub_agent": "qwen3.5-32b",
    "compaction": "qwen3.5-7b",
    "fallback": "qwen3.5-7b"
  },
  "skill_router": {
    "enabled": true,
    "tier_a": true,
    "tier_b": true,
    "tier_c": false
  },
  "diminishing_returns": {
    "enabled": true,
    "min_continuations": 3,
    "min_delta_tokens": 500
  },
  "swarm": {
    "enabled": true,
    "synthesis_enabled": true,
    "max_members": 5
  },
  "thinking": { "mode": "adaptive", "budget_tokens": 10000 },
  "dream": { "enabled": true, "min_turns": 3 },
  "hooks": []
}
```

### Config locations (low → high precedence)

1. `~/.llmcode/config.json` — User global
2. `.llmcode/config.json` — Project
3. `.llmcode/config.local.json` — Local (gitignored)
4. CLI flags / env vars

### Lazy / scoped MCP servers

`mcpServers` now supports a split schema so heavy MCP servers start only
when a persona or skill that needs them is invoked (gated by an in-TUI
approval prompt). Legacy flat configs still work — every entry is treated
as `always_on`.

```json
{
  "mcpServers": {
    "always_on": {
      "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
    },
    "on_demand": {
      "tavily": {
        "command": "npx",
        "args": ["-y", "tavily-mcp"],
        "env": { "TAVILY_API_KEY": "$TAVILY_API_KEY" }
      },
      "browser": {
        "command": "npx",
        "args": ["-y", "@browsermcp/mcp"]
      }
    }
  }
}
```

A persona declares which `on_demand` servers it needs via its
`mcp_servers` tuple (see `llm_code/swarm/personas/web_researcher.py`);
a skill can declare the same via an `mcp_servers:` list in its SKILL.md
frontmatter. Persona-scoped servers are torn down when the persona
finishes; skill-scoped servers live for the session.

### Optional features

```bash
pip install llmcode-cli[voice]          # Voice input via STT
pip install llmcode-cli[computer-use]   # GUI automation
pip install llmcode-cli[ide]            # IDE integration
pip install llmcode-cli[telemetry]      # OpenTelemetry tracing
pip install llmcode-cli[treesitter]     # Tree-sitter multi-language repo map
```

---

## Docs

- [Memory system](docs/memory.md) — 5-layer architecture, typed taxonomy, DreamTask
- [Coordinator](docs/coordinator.md) — synthesis-first orchestration, resume mechanism
- [Architecture](docs/architecture.md) — high-level system overview
- [Plugins](docs/plugins.md) — building plugins
- [Tools](docs/tools.md) — tool reference
- [Configuration](docs/configuration.md) — all config options

---

## Architecture

```
llm_code/               48,000+ lines Python
├── api/                Provider abstraction (OpenAI-compat + Anthropic)
├── cli/                CLI entry point, TUI launcher, oneshot modes (-x/-q)
│   └── templates/      LLM-driven command templates (init.md, etc)
├── runtime/            ReAct engine, 5-layer memory, skill router,
│                       compression, hooks, permissions, checkpoint,
│                       dream, VCR, speculative execution, telemetry,
│                       file protection, sandbox, secret scanner,
│                       conversation DB, tree-sitter repo map
│   └── prompts/        Per-model system prompts (anthropic, gpt,
│                       gemini, qwen, llama, deepseek, kimi, codex)
├── tools/              30+ tools with deferred loading + security
├── task/               PLAN/DO/VERIFY/CLOSE state machine
├── hida/               Dynamic context loading (10-type classifier)
├── mcp/                MCP client (4 transports) + OAuth + health checks
├── marketplace/        Plugin system + security scanning
├── lsp/                Language Server Protocol client
├── remote/             WebSocket server/client + SSH proxy
├── vim/                Vim engine
├── voice/              STT (Whisper, Google, Anthropic backends)
├── computer_use/       GUI automation
├── cron/               Task scheduler
├── ide/                IDE bridge (WebSocket JSON-RPC)
├── swarm/              Multi-agent coordinator (synthesis-first)
└── utils/              Notebook, diff, hyperlinks, search
tests/                  5,160+ tests across 418 files
```

---

## Contributing

```bash
git clone https://github.com/DJFeu/llmcode
cd llmcode
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                  # 5,160+ tests
ruff check llm_code/    # lint
```

Looking for contributors interested in:

- More provider integrations (Anthropic native, OpenAI, Google, xAI, DeepSeek)
- More built-in skills (especially for Python-specific workflows)
- IDE integrations (VS Code, JetBrains, Neovim)
- i18n / l10n
- Per-model prompt tuning for additional model families
- Documentation, tutorials, examples
- Real-world usage feedback (especially on local Qwen/Llama/DeepSeek)

### Requirements

- Python 3.11+
- An LLM server (vLLM, Ollama, LM Studio, or any OpenAI-compatible cloud API)

---

## License

MIT

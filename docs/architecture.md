# Architecture

## Layer Diagram

```
cli → runtime → tools
         ↓
        api
         ↓
    mcp / lsp / marketplace
```

## Package Map

| Package | Responsibility |
|---------|---------------|
| `api/` | LLM provider abstraction (OpenAI-compat + Anthropic) |
| `tools/` | 20 built-in tools + agent + parsing |
| `runtime/` | Conversation loop, permissions, hooks, session, memory, compression |
| `mcp/` | MCP client, tool bridge, server lifecycle |
| `marketplace/` | Plugin system, 5 registries |
| `lsp/` | LSP client, auto-detector, 3 tools |
| `cli/` | REPL, streaming renderer, commands |

## Key Design Decisions

1. **Strict layer deps** — cli→runtime→{tools,api}, no reverse deps
2. **Immutable data** — Frozen dataclasses everywhere, new session on each mutation
3. **Fail-closed safety** — Tools default to not-read-only, not-concurrent-safe
4. **Dual-track tools** — Native function calling + XML tag fallback for any model
5. **Progressive compression** — 4 levels, lightweight first

# Hayhooks — headless transports

`llmcode hayhooks serve` exposes the llmcode engine as:

- **MCP server** (stdio or SSE) — consumable by Claude Desktop / any
  MCP client.
- **OpenAI-compatible HTTP endpoint** — drop-in for the `openai` SDK,
  LangChain `ChatOpenAI`, litellm, and internal tools that speak the
  OpenAI wire protocol.

## Install

```
pip install llmcode[hayhooks]
```

## Quickstart

```bash
# MCP over stdio (default)
llmcode hayhooks serve

# OpenAI-compatible endpoint on 127.0.0.1:8080
LLMCODE_HAYHOOKS_TOKEN=$(openssl rand -hex 24) \
  llmcode hayhooks serve --transport openai --port 8080
```

## Defaults

- Bind address defaults to `127.0.0.1`. Use `--allow-remote` to bind
  other interfaces (reverse-proxy with TLS strongly recommended).
- Bearer token required for every authenticated HTTP route.
- Rate limit defaults to 60 rpm per session fingerprint.

## M4.11 migrations

Hayhooks absorbs the legacy `llm_code.remote` and `llm_code.ide`
packages. IDE clients must update their base URL to
`ws://host:<port>/ide/rpc`; remote REPL clients now target the optional
`/debug/repl` sub-app (disabled by default).

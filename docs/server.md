# Formal client/server (v16 M9)

`llmcode server` is a JSON-RPC 2.0 over WebSocket surface that lets
multiple clients share one running session. One writer drives the
conversation; N observers stream the same events. Everything is
authenticated by HMAC-signed bearer tokens persisted in a SQLite WAL
store at `~/.llmcode/server/tokens.db`.

> The legacy debug REPL at `llmcode --serve` is unchanged. The new
> server is `llmcode server start` — different surfaces, different
> defaults.

## Quick start

```bash
# 1. Mint an admin token (session_id="*" lets it create new sessions).
llmcode server token grant '*' --role writer --ttl 3600

# 2. Start the server in one terminal.
llmcode server start --host 127.0.0.1 --port 8080

# 3. Connect from another terminal (paste the token from step 1).
llmcode connect ws://127.0.0.1:8080 --token <token>
```

## Methods

| Method | Description |
|---|---|
| `session.create` | Mint a fresh session. Requires admin scope (`session_id="*"`). |
| `session.attach` | Join a session as `writer` or `observer`. Pass `last_event_id` to resume. |
| `session.send` | Forward a user message. Writer-only. |
| `session.subscribe_events` | Marker call; `attach` already wires the queue. |
| `session.fork` | Deep-copy a session under a new id. Writer-only. |
| `session.detach` | Release the caller's role on a session. |
| `session.close` | Tear down the session. Writer-only. |

All methods follow the JSON-RPC 2.0 spec: requests carry
`{"jsonrpc": "2.0", "id", "method", "params"}`, responses carry
either `result` or `error`.

## Multi-client semantics

* Exactly one writer per session at a time. A second `writer` attach
  by a different `client_id` returns error code `-32002`
  (`WRITER_CONFLICT`).
* Re-attach by the same `client_id` is a no-op — useful after a
  reconnect.
* A writer attaching as `observer` drops the writer slot first, so a
  second writer can take over.

## Reconnect with `last_event_id`

Each session keeps the last 1000 events in a ring buffer. A
reconnecting client sends the highest `event_id` it saw; the server
replays everything after that and returns it under
`result.replayed`. If the cursor is older than the oldest buffered
event the server returns error code `-32004` (`EVENTS_EVICTED`) and
the client should drop its local state and re-attach with
`last_event_id=0`.

The Python client library at `llm_code.server.client.ServerClient`
handles this automatically — it retries `attach` once on
`EVENTS_EVICTED` after invoking the optional `on_evicted` callback so
callers can flush whatever buffer they were maintaining.

## Token management

```bash
llmcode server token grant <session_id> --role writer --ttl 3600
llmcode server token revoke <token>
llmcode server token list
```

Tokens are HMAC-signed JSON payloads. Validation hits the SQLite row
on every request, so revocation is immediate.

The HMAC secret defaults to a per-store random value. Set
`LLMCODE_SERVER_TOKEN_SECRET` (32+ ascii bytes) to share a secret
across hosts.

## Logging discipline

Bearer tokens never appear in logs. Every entry that needs to
identify a token uses the 8-char SHA-256 prefix
(`token_fingerprint`). The token CLI's `list` subcommand exposes
fingerprints only — full tokens are returned only at `grant` time.

## Error codes

| Code | Name | Meaning |
|---|---|---|
| `-32700` | `PARSE_ERROR` | Frame is not valid JSON. |
| `-32600` | `INVALID_REQUEST` | Frame is missing required fields. |
| `-32601` | `METHOD_NOT_FOUND` | Unknown `method`. |
| `-32602` | `INVALID_PARAMS` | Unknown role / bad shape. |
| `-32603` | `INTERNAL_ERROR` | Unhandled exception. |
| `-32001` | `UNAUTHORIZED` | Missing / bad / revoked / cross-scope token. |
| `-32002` | `WRITER_CONFLICT` | Second writer attach. |
| `-32003` | `SESSION_NOT_FOUND` | Session id unknown. |
| `-32004` | `EVENTS_EVICTED` | `last_event_id` past the ring buffer. |

## Operations

* The server PID is written to `~/.llmcode/server/server.pid`. Use
  `llmcode server stop` to send SIGTERM to that pid.
* The websocket transport is bound to `127.0.0.1` by default. Pass
  `--host 0.0.0.0` to listen on all interfaces — only do this on
  trusted networks.
* The server requires the optional `[websocket]` extra. Install with
  `pip install llmcode-cli[websocket]`.

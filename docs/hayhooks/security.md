# Hayhooks Security

> **Status:** v12 M4 — safe defaults out of the box. Do not relax
> them without re-running the pen-test checklist at the bottom.

## Section table of contents

1. Defaults
2. Token management
3. TLS + reverse proxy
4. Authentication details
5. Rate limiting
6. Body + message size caps
7. Tool allow-list
8. Prompt injection caveats
9. Pen-test checklist

## 1. Defaults

Hayhooks ships with these guard rails enabled; changing any of
them without a corresponding mitigation is **not recommended**:

- Bind address `127.0.0.1` unless `--allow-remote` is passed.
- Bearer token required for every authenticated route.
- Rate limit: 60 requests/minute per session fingerprint.
- Request body capped at 1 MB.
- Message count capped at 100 per request.
- Bearer token compared in constant time (`hmac.compare_digest`).
- `cors_origins` empty — cross-origin requests rejected.
- `enable_debug_repl` off; the migrated `remote/` debug REPL is
  opt-in and disabled by default.

## 2. Token management

The bearer token is read from an environment variable named by
`HayhooksConfig.auth_token_env` (default:
`LLMCODE_HAYHOOKS_TOKEN`). Generate a strong, high-entropy
secret and rotate it on a fixed cadence:

```bash
export LLMCODE_HAYHOOKS_TOKEN=$(openssl rand -hex 32)
```

Rules of hygiene:

- **Never** pass the token via `--auth-token` or any CLI flag —
  other users on the host can read `/proc/<pid>/cmdline`.
- **Never** check the token into config files, .env files
  committed to git, or Dockerfile `ENV` lines. Use a secret
  manager (1Password, Vault, AWS Secrets Manager, GCP Secret
  Manager) or environment injection at process start.
- **Rotate** after any of: suspected leak, personnel change,
  team offboarding, or `grep`-able appearance in shell history.
- Prefer **one token per consumer**. Claude Desktop on your
  laptop and a CI pipeline should not share a token; revoking
  one should not disrupt the other.

## 3. TLS + reverse proxy

The bundled `uvicorn` server does not terminate TLS. When
exposing hayhooks beyond `localhost`, always front it with a
reverse proxy that manages a valid certificate.

### Caddy (recommended for simplicity)

```caddy
hayhooks.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy fetches + auto-renews Let's Encrypt certificates. HSTS and
HTTP→HTTPS redirection are on by default.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name hayhooks.example.com;

    ssl_certificate     /etc/letsencrypt/live/hayhooks.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hayhooks.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location / {
        proxy_pass       http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE-friendly settings
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_buffering    off;
        proxy_read_timeout 1h;
    }
}
```

Never bind hayhooks itself to `0.0.0.0` even behind a proxy —
keep it on `127.0.0.1` or a private-network bind so a proxy
misconfiguration does not expose the origin directly.

## 4. Authentication details

Every authenticated route requires `Authorization: Bearer
<token>`. Missing or malformed headers → `401`. Wrong token →
`401` (the logged message includes only the SHA-256 fingerprint
of the received token, never the raw value — see
`llm_code/hayhooks/auth.py`).

Unauthenticated routes:

- `GET /v1/health` — liveness probe for load balancers.
- `/metrics` — Prometheus scrape; bind to an internal-only port
  in production (or front with a proxy that enforces network
  ACLs).

All other routes (OpenAI `/v1/*`, MCP `/sse`, IDE RPC, debug
REPL) require the bearer.

## 5. Rate limiting

Default: 60 requests/minute per session fingerprint (a hash of
bearer token + IP). Tunable via
`HayhooksConfig.rate_limit_rpm`. Exceeding the cap returns `429`
with `Retry-After` set — clients using the `RetryOnRateLimit`
policy (see
[engine/policy_author_guide.md](../engine/policy_author_guide.md))
honour the header automatically.

Set a lower cap for public deployments; the current default is
tuned for trusted interactive use.

## 6. Body + message size caps

- Request body: 1 MB → `413 Payload Too Large` otherwise.
- Message count: 100 → `400 Bad Request` otherwise.
- SSE response chunks: capped at 64 KiB each so a malicious
  upstream cannot starve the serializer.

## 7. Tool allow-list

`HayhooksConfig.allowed_tools: tuple[str, ...]` restricts the
tool surface the agent can call. Use this for any deployment
exposed beyond a single developer:

```json
{
  "engine": {
    "hayhooks": {
      "enabled": true,
      "allowed_tools": ["read_file", "grep_search", "glob_search"],
      "max_agent_steps": 10,
      "rate_limit_rpm": 30
    }
  }
}
```

Tools not in the allow-list are hidden from the agent's tool
catalog and rejected at the `PermissionCheck` Component even if
the model hallucinates a call.

## 8. Prompt injection caveats

Hayhooks does not sanitise prompt content — any user-supplied
text reaches the model verbatim. Authority boundaries (which
tools, which files, which permissions) are **your** responsibility:

- Set a conservative `allowed_tools` list.
- Keep `max_agent_steps` low (10 is a reasonable ceiling for
  untrusted input).
- Disable write tools (`edit_file`, `bash`) on deployments that
  accept prompts from end users.
- Front the deployment with an input validator for known-risky
  patterns (e.g. embedded system prompts, jailbreak strings).
- Enable the redacting log filter (`redact_log_records = true`)
  so accidental leaks don't end up in observability.

## 9. Pen-test checklist

Re-run before shipping any hayhooks deployment:

- [ ] `curl -X POST http://host/v1/chat/completions` without
  `Authorization` → `401`.
- [ ] `curl` with a wrong token → `401`; response body does not
  leak internals; logs show only the token fingerprint.
- [ ] `curl` with the correct token but an oversized body (> 1 MB)
  → `413`.
- [ ] `curl` with > 100 messages → `400`.
- [ ] Attempt to start hayhooks with `--host 0.0.0.0` but without
  `--allow-remote` → startup refused.
- [ ] Exceed `rate_limit_rpm` → `429` with `Retry-After` header.
- [ ] Without a TLS frontend, attempt to MitM the bearer token
  → confirm the traffic is unreadable (should be — behind TLS).
- [ ] Tool call outside `allowed_tools` → rejected by
  `PermissionCheck`; denial is logged (scrubbed) and reaches the
  observability pipeline.
- [ ] `GET /metrics` is reachable only from the internal network
  / only through the reverse proxy's internal path.

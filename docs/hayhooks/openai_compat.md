# OpenAI-compatible transport

Hayhooks serves an OpenAI-shaped HTTP API at `/v1/*`:

- `POST /v1/chat/completions` — non-streaming + SSE streaming.
- `GET /v1/models` — lists llmcode profile names.
- `GET /v1/health` — unauthenticated liveness probe.

## Auth

Every route except `/v1/health` requires `Authorization: Bearer <token>`.
The expected token is read from the env var named by
`HayhooksConfig.auth_token_env` (default: `LLMCODE_HAYHOOKS_TOKEN`).

Tokens are compared in constant time and never logged.

## Error envelope

All errors match OpenAI's shape:

```json
{"error": {"message": "...", "type": "...", "code": "..."}}
```

## Usage

### curl

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $LLMCODE_HAYHOOKS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"llmcode-default","messages":[{"role":"user","content":"hi"}]}'
```

### openai Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.environ["LLMCODE_HAYHOOKS_TOKEN"],
)

resp = client.chat.completions.create(
    model="llmcode-default",
    messages=[{"role": "user", "content": "explain FizzBuzz"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

## Unsupported fields

The endpoint accepts (and silently ignores) function-calling parameters
(`functions`, `tools`), log-probabilities, multi-sample (`n>1`), and
frequency/presence penalties. llmcode runs its tools server-side and
does not surface the catalog through this API.

## Size limits

- 1 MB request body (`413` otherwise).
- 100 messages per request (`400` otherwise).

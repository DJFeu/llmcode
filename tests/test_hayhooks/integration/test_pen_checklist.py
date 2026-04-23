"""M4 Task 4.8 Step 4 — hayhooks penetration-test checklist.

Each case drives the FastAPI app through an ``httpx.AsyncClient`` over
``ASGITransport`` (no sockets, no live server) so the suite runs
deterministically inside ``pytest`` while still exercising the exact
ASGI path a real uvicorn deploy would hit.

Cases, per the ship-criteria checklist in plan #4 Task 4.8 Step 4:

1. No Authorization header → 401 with OpenAI-shape error envelope.
2. Wrong Authorization bearer → 401.
3. Correct bearer + valid body → 200 (mock Agent).
4. Request body > 1 MB → 413.
5. ``messages`` array longer than 100 entries → 400.
6. 61 requests inside a 60-second window → 429 with ``Retry-After``
   header set.
7. CLI ``--host 0.0.0.0`` without ``--allow-remote`` → click raises a
   clear ``UsageError``.

All HTTP cases share a single FastAPI app fixture constructed with a
session factory that injects a mock Agent, so nothing touches the
outside world.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx

from llm_code.hayhooks.cli import hayhooks_serve
from llm_code.hayhooks.openai_compat import build_app
from llm_code.hayhooks.session import HayhooksSession


# ── Shared app / client fixtures ─────────────────────────────────────


@pytest.fixture
def app(hayhooks_config, mock_agent, bearer_env):
    """Build the FastAPI app with a session factory that returns a
    HayhooksSession wired to the mock Agent."""

    def _factory(config, fingerprint):
        return HayhooksSession(
            config=config, fingerprint=fingerprint, agent=mock_agent,
        )

    return build_app(hayhooks_config, session_factory=_factory)


@pytest.fixture
async def client(app):
    """``httpx.AsyncClient`` over ``ASGITransport`` — live server sim."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as c:
        yield c


def _chat_payload(*, messages_count: int = 1, content: str = "hi"):
    return {
        "model": "llmcode-default",
        "messages": [
            {"role": "user", "content": content}
            for _ in range(messages_count)
        ],
    }


# ── Pen-test cases ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPenChecklist:
    """One test per row of the M4 pen-test ship checklist."""

    async def test_missing_auth_header_returns_401_with_openai_envelope(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await client.post(
            "/v1/chat/completions", json=_chat_payload(),
        )
        assert r.status_code == 401
        body = r.json()
        # OpenAI error envelope shape:
        # {"error": {"message": ..., "type": "authentication_error", "code": ...}}
        assert "error" in body
        assert set(body["error"].keys()) >= {"message", "type", "code"}
        assert body["error"]["type"] == "authentication_error"

    async def test_wrong_bearer_token_returns_401(
        self, client: httpx.AsyncClient, bearer_env: str,
    ) -> None:
        r = await client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["type"] == "authentication_error"

    async def test_correct_bearer_and_valid_body_returns_200(
        self, client: httpx.AsyncClient, bearer_env: str, mock_agent,
    ) -> None:
        mock_agent.text = "pen-test-pass"
        r = await client.post(
            "/v1/chat/completions",
            json=_chat_payload(content="what is 2+2?"),
            headers={"Authorization": f"Bearer {bearer_env}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["object"] == "chat.completion"
        assert (
            body["choices"][0]["message"]["content"] == "pen-test-pass"
        )

    async def test_oversized_body_rejected_with_413(
        self, client: httpx.AsyncClient, bearer_env: str,
    ) -> None:
        """Payload > 1 MB (per ``_MAX_PAYLOAD_BYTES`` in openai_compat)
        must be refused with HTTP 413 — no agent allocation."""
        huge_content = "x" * 1_000_050  # 1 MB + 50 B overhead
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {bearer_env}"},
            content=json.dumps({
                "model": "llmcode-default",
                "messages": [{"role": "user", "content": huge_content}],
            }),
        )
        assert r.status_code == 413
        body = r.json()
        assert body["error"]["code"] == "payload_too_large"

    async def test_more_than_100_messages_rejected_with_400(
        self, client: httpx.AsyncClient, bearer_env: str,
    ) -> None:
        r = await client.post(
            "/v1/chat/completions",
            json=_chat_payload(messages_count=101),
            headers={"Authorization": f"Bearer {bearer_env}"},
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error"]["type"] == "invalid_request_error"
        assert "100" in body["error"]["message"]

    async def test_61_requests_in_60s_returns_429_with_retry_after(
        self,
        hayhooks_config,
        bearer_env: str,
        mock_agent,
        app,
    ) -> None:
        """Exceeding ``rate_limit_rpm`` inside a single logical session
        must yield HTTP 429 with a ``Retry-After`` header advising how
        long to back off. Session state is keyed by auth fingerprint,
        so all 61 requests must reuse the same bearer — which is the
        default behaviour when the client drives the one shared app.

        Note: the default ``rate_limit_rpm`` is 60 in the fixture; the
        61st request inside the same sliding window is the one that
        trips the limit.
        """
        # Replace the session factory with one that reuses a single
        # HayhooksSession across all requests — this simulates 61 hits
        # from the same authenticated client inside 60 s. A fresh
        # per-request session would reset the sliding window and
        # defeat the test.
        shared_session = HayhooksSession(
            config=hayhooks_config,
            fingerprint="pen-test-fp",
            agent=mock_agent,
        )

        # Build a fresh app around the shared session so it really is
        # shared — not the per-call factory used elsewhere.
        def _shared_factory(config, fingerprint):  # noqa: ARG001
            return shared_session

        shared_app = build_app(
            hayhooks_config, session_factory=_shared_factory,
        )
        transport = httpx.ASGITransport(app=shared_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as c:
            headers = {"Authorization": f"Bearer {bearer_env}"}
            # 60 successful requests ought to all land under the limit.
            for _ in range(60):
                ok = await c.post(
                    "/v1/chat/completions",
                    json=_chat_payload(),
                    headers=headers,
                )
                assert ok.status_code == 200, (
                    f"unexpected status inside 60 rpm window: "
                    f"{ok.status_code} / {ok.text}"
                )

            # The 61st request must trip the limiter.
            over = await c.post(
                "/v1/chat/completions",
                json=_chat_payload(),
                headers=headers,
            )
            assert over.status_code == 429, over.text
            assert "Retry-After" in over.headers, (
                f"429 response missing Retry-After header: "
                f"{dict(over.headers)}"
            )
            # Retry-After must parse as a positive integer (seconds per
            # RFC 7231 §7.1.3 — we don't emit HTTP-date form).
            retry_after = int(over.headers["Retry-After"])
            assert retry_after >= 1

            body = over.json()
            assert body["error"]["type"] == "rate_limit_error"
            assert body["error"]["code"] == "rate_limit_exceeded"


class TestRemoteBindRefusal:
    """CLI-level pen-test: attempting to bind a non-loopback host
    without ``--allow-remote`` must fail with a *clear* error message,
    not a silent success."""

    def test_bind_to_zero_zero_without_flag_raises_clear_error(self) -> None:
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(
            hayhooks_serve,
            ["serve", "--transport", "openai", "--host", "0.0.0.0"],
        )
        assert result.exit_code != 0
        # Error message must mention the exact flag name so operators
        # know how to opt in.
        assert "allow-remote" in result.output.lower()
        # And the refused host must appear in the message so logs make
        # the intent obvious during a post-mortem.
        assert "0.0.0.0" in result.output

    def test_bind_to_public_ip_without_flag_raises_clear_error(
        self,
    ) -> None:
        """Belt-and-suspenders: a typo that binds a public IP must
        also be refused, not just the obvious ``0.0.0.0`` case."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(
            hayhooks_serve,
            ["serve", "--transport", "openai", "--host", "192.0.2.1"],
        )
        assert result.exit_code != 0
        assert "allow-remote" in result.output.lower()

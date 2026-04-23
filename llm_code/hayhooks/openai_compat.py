"""OpenAI-compatible HTTP endpoints exposed by hayhooks.

Implements:

- ``POST /v1/chat/completions`` — non-streaming + SSE streaming.
- ``GET /v1/models`` — lists llmcode model profiles.
- ``GET /v1/health`` — unauthenticated health probe.

Plus, under Task 4.11, mounts the ported IDE RPC WebSocket as
``/ide/rpc`` and an optional debug REPL WebSocket as ``/debug/repl``.

Error envelopes follow the OpenAI spec:

    {"error": {"message": "...", "type": "...", "code": "..."}}
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

try:  # pragma: no cover — FastAPI only at runtime
    from fastapi import Depends, FastAPI, HTTPException, Request, status
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FastAPI = Depends = HTTPException = Request = status = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]

    def Field(*args, **kwargs):  # type: ignore[no-redef]
        return None

    _FASTAPI_AVAILABLE = False

from llm_code.hayhooks.auth import verify_token
from llm_code.hayhooks.errors import (
    BadRequestError,
    HayhooksError,
    InvalidTokenError,
    MissingTokenError,
    PayloadTooLargeError,
    RateLimitError,
    envelope_from_exc,
)
from llm_code.hayhooks.session import HayhooksSession, RateLimitExceeded
from llm_code.hayhooks.streaming import agent_events_to_sse_lines

# Defensive caps — mirror the pen-test checklist.
_MAX_MESSAGES = 100
_MAX_PAYLOAD_BYTES = 1_000_000  # 1 MB


class ChatMessage(BaseModel):  # type: ignore[misc]
    role: str
    content: str


class ChatRequest(BaseModel):  # type: ignore[misc]
    model: str = "llmcode-default"
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None

    class Config:
        extra = "allow"


def _require_fastapi() -> None:
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi is not installed; run `pip install llmcode[hayhooks]`"
        )


def build_app(
    config: Any,
    *,
    session_factory=HayhooksSession,
) -> Any:
    """Build a FastAPI app for the OpenAI-compatible endpoints.

    ``session_factory`` is injectable so tests can pass a fake that
    wraps a mocked Agent.
    """
    _require_fastapi()

    app = FastAPI(title="llmcode-hayhooks (OpenAI-compat)")

    # --- M6 observability: root span per HTTP request --------------
    # Guarded import so a missing observability install never breaks
    # hayhooks boot. The middleware is a no-op when OTel isn't set up.
    try:
        from llm_code.engine.observability.attributes import SESSION_ID
        from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]

        _tracer = _otel_trace.get_tracer("llmcode.hayhooks")

        @app.middleware("http")
        async def _trace_middleware(request: Request, call_next):  # type: ignore[valid-type]
            route = request.url.path
            with _tracer.start_as_current_span(
                f"hayhooks.{request.method} {route}",
                attributes={
                    "http.method": request.method,
                    "http.route": route,
                },
            ) as span:
                session_header = request.headers.get("x-llmcode-session-id")
                if session_header:
                    try:
                        span.set_attribute(SESSION_ID, session_header)
                    except Exception:  # pragma: no cover - defensive
                        pass
                response = await call_next(request)
                try:
                    span.set_attribute("http.status_code", response.status_code)
                except Exception:  # pragma: no cover
                    pass
                return response
    except Exception:  # pragma: no cover - OTel missing / partial install
        pass

    # --- error handlers --------------------------------------------

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException):  # type: ignore[valid-type]
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": detail,
                    "type": "authentication_error"
                    if exc.status_code == 401 else "invalid_request_error",
                    "code": "http_error",
                }
            },
        )

    async def _hayhooks_error_handler(_request: Request, exc: Exception):  # type: ignore[valid-type]
        """Map ``HayhooksError`` (and plain ``Exception`` fallbacks) to
        the OpenAI-shape error envelope plus the right HTTP status.

        Must be registered via :meth:`FastAPI.add_exception_handler`
        (class-keyed) rather than the ``@app.exception_handler(Exception)``
        decorator — Starlette 1.0 changed the decorator dispatch so that
        the broad ``Exception`` handler is no longer invoked for
        ordinary ``Exception`` subclasses raised from route handlers.
        Registering per-class re-instates the expected behaviour.
        """
        status_code, body = envelope_from_exc(exc)
        headers: dict[str, str] = {}
        # RFC 7231 §7.1.3: 429 responses SHOULD carry Retry-After so
        # well-behaved clients back off for the advertised window.
        retry_after = getattr(exc, "retry_after", None)
        if status_code == 429 and retry_after is not None:
            try:
                # Whole seconds per RFC; round up so clients never retry
                # a tick too early and re-trip the limiter.
                headers["Retry-After"] = str(max(1, int(float(retry_after) + 0.999)))
            except (TypeError, ValueError):  # pragma: no cover - defensive
                pass
        return JSONResponse(
            status_code=status_code, content=body, headers=headers or None,
        )

    # Register for the base class so every subclass inherits the mapping.
    app.add_exception_handler(HayhooksError, _hayhooks_error_handler)
    # Also register for Exception so unexpected crashes get the OpenAI
    # envelope treatment (500 with a generic message) — still required
    # for the ship-criterion error shape.
    app.add_exception_handler(Exception, _hayhooks_error_handler)

    # --- unauthenticated routes ------------------------------------

    @app.get("/v1/health")
    async def health() -> dict:
        return {"status": "ok"}

    # --- M6 observability: Prometheus /metrics endpoint ------------
    # Guarded so a missing prometheus_client or observability module
    # never breaks hayhooks boot.
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # type: ignore[import-not-found]

        from llm_code.engine.observability.metrics import registry as _prom_registry

        if _prom_registry is not None:
            from fastapi import Response  # type: ignore[import-not-found]

            @app.get("/metrics")
            async def metrics() -> Any:
                return Response(
                    content=generate_latest(_prom_registry),
                    media_type=CONTENT_TYPE_LATEST,
                )
    except Exception:  # pragma: no cover - optional dep missing
        pass

    # --- authenticated routes --------------------------------------

    async def _auth(authorization: str | None = None) -> str:
        # Shim so the FastAPI dep can read the raw header reliably.
        try:
            return verify_token(
                authorization,
                env_var=getattr(config, "auth_token_env", None),
            )
        except (MissingTokenError, InvalidTokenError) as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc

    async def _auth_dep(request: Request) -> str:  # type: ignore[valid-type]
        return await _auth(request.headers.get("authorization"))

    @app.get("/v1/models")
    async def list_models(fp: str = Depends(_auth_dep)) -> dict:
        return {
            "object": "list",
            "data": _list_profile_entries(),
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,  # type: ignore[valid-type]
        fp: str = Depends(_auth_dep),
    ):
        raw = await request.body()
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(
                f"payload exceeds {_MAX_PAYLOAD_BYTES} bytes"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequestError(f"invalid JSON body: {exc}") from exc

        try:
            body = ChatRequest.model_validate(payload)
        except Exception as exc:  # pydantic ValidationError
            raise BadRequestError(str(exc)) from exc

        if len(body.messages) > _MAX_MESSAGES:
            raise BadRequestError(
                f"too many messages (>{_MAX_MESSAGES})"
            )

        session = session_factory(config=config, fingerprint=fp)
        messages = [m.model_dump() for m in body.messages]

        try:
            if body.stream:
                return _stream_response(session, messages, body.model)
            result = await session.run_async(messages)
            return _non_streaming_envelope(result, body.model)
        except RateLimitExceeded as exc:
            raise RateLimitError(
                str(exc), retry_after=getattr(exc, "retry_after", None),
            ) from exc

    # --- mount migrated sub-apps -----------------------------------

    _mount_ide_rpc(app, config)
    _mount_debug_repl(app, config)

    return app


def _list_profile_entries() -> list[dict]:
    """Return ``{"id":..., "object":"model"}`` entries for /v1/models."""
    try:
        from llm_code.runtime.model_profile import list_profiles
        profiles = list_profiles() or []
    except Exception:
        profiles = []
    entries = [
        {
            "id": getattr(p, "name", None) or str(p),
            "object": "model",
            "owned_by": "llmcode",
        }
        for p in profiles
    ]
    if not entries:
        entries = [
            {"id": "llmcode-default", "object": "model", "owned_by": "llmcode"},
        ]
    return entries


def _non_streaming_envelope(result: Any, model: str) -> dict:
    final = getattr(result, "final_text", None)
    text = final() if callable(final) else str(getattr(result, "text", ""))
    pt = int(getattr(result, "prompt_tokens", 0) or 0)
    ct = int(getattr(result, "completion_tokens", 0) or 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": getattr(result, "exit_reason", "stop") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
        },
    }


def _stream_response(session: HayhooksSession, messages: list[dict], model: str):
    """Return an ``EventSourceResponse`` driven by the session stream."""
    from sse_starlette.sse import EventSourceResponse

    async def _gen():
        events = session.run_streaming(messages)
        async for line in agent_events_to_sse_lines(events, model):
            yield line

    return EventSourceResponse(_gen())


def _mount_ide_rpc(app: Any, config: Any) -> None:
    if not getattr(config, "enable_ide_rpc", True):
        return
    try:
        from llm_code.hayhooks.ide_rpc import register_ide_routes
    except Exception:
        return
    register_ide_routes(app)


def _mount_debug_repl(app: Any, config: Any) -> None:
    if not getattr(config, "enable_debug_repl", False):
        return
    try:
        from llm_code.hayhooks.debug_repl import register_debug_repl_routes
    except Exception:
        return
    register_debug_repl_routes(app, config)


def run_openai(config: Any, host: str, port: int) -> None:
    """Launch uvicorn against :func:`build_app`."""
    import uvicorn

    app = build_app(config)
    uvicorn.run(app, host=host, port=port)

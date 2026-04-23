"""Provider client factory — routes model names to the correct provider.

M5 note: providers already expose ``async def stream_message`` (see
:class:`llm_code.api.provider.LLMProvider`), so the module-level
:func:`stream_async` helper is a thin facade that picks the provider
for a given model name and yields its async stream events. The legacy
sync :func:`stream` helper bridges into the async path via an internal
thread running ``asyncio.run`` — kept as a **compat shim** for callers
that haven't migrated yet and removed in M8.b alongside the v12 flag.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from llm_code.api.provider import LLMProvider
from llm_code.api.provider_registry import ProviderDescriptor, get_registry
from llm_code.api.types import MessageRequest, StreamEvent
from llm_code.logging import get_logger
from llm_code.runtime.model_aliases import resolve_model
from llm_code.runtime.model_profile import ModelProfile, get_profile

logger = get_logger(__name__)


@dataclass(frozen=True)
class DescribeResult:
    """Everything callers need to know about a model's wiring.

    Returned by :meth:`ProviderClient.describe`. The ``descriptor`` is
    ``None`` only when the model's profile points at a ``provider_type``
    that has no registered :class:`ProviderDescriptor` — in which case
    ``describe`` also logs a warning so the gap is visible.
    """

    model: str
    profile: ModelProfile
    descriptor: ProviderDescriptor | None


class ProviderClient:
    """Factory for creating LLMProvider instances based on model name."""

    @classmethod
    def describe(
        cls,
        model: str,
        custom_aliases: dict[str, str] | None = None,
    ) -> DescribeResult:
        """Resolve ``model`` to its profile + registered descriptor.

        Useful for callers that need capability metadata (prompt cache
        support, tools_format, max context) without instantiating a
        provider. Logs a warning when the profile's ``provider_type``
        has no descriptor in the registry.
        """
        resolved = resolve_model(model, custom_aliases)
        profile = get_profile(resolved)
        descriptor: ProviderDescriptor | None = None
        if profile.provider_type:
            descriptor = get_registry().get(profile.provider_type)
            if descriptor is None:
                logger.warning(
                    "No ProviderDescriptor registered for provider_type=%r "
                    "(model=%r); add one via provider_registry.register() "
                    "so capability lookups resolve.",
                    profile.provider_type,
                    resolved,
                )
        return DescribeResult(model=resolved, profile=profile, descriptor=descriptor)

    @staticmethod
    def from_model(
        model: str,
        base_url: str = "",
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        native_tools: bool = True,
        custom_aliases: dict[str, str] | None = None,
    ) -> LLMProvider:
        """Return the appropriate LLMProvider for the given model name.

        Routing uses the model profile system: the profile's
        ``provider_type`` field determines which provider class to
        instantiate, and ``native_tools`` overrides the caller's
        default when the profile declares it.
        """
        # Go through describe() so the registry warning fires for
        # provider types that lack a descriptor — same code path for
        # lookup and instantiation keeps the two layers aligned.
        spec = ProviderClient.describe(model, custom_aliases=custom_aliases)
        resolved_model = spec.model
        profile = spec.profile

        if profile.provider_type == "anthropic":
            return ProviderClient._make_anthropic(
                model=resolved_model,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )

        # Profile overrides native_tools when explicitly declared.
        # Built-in Qwen profiles set native_tools=False so the
        # caller's default (True) doesn't override the profile.
        effective_native_tools = profile.native_tools if profile.name else native_tools

        return ProviderClient._make_openai_compat(
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            native_tools=effective_native_tools,
        )

    # ------------------------------------------------------------------
    # Private factory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_openai_compat(
        model: str,
        base_url: str,
        api_key: str,
        timeout: float,
        max_retries: int,
        native_tools: bool,
    ) -> LLMProvider:
        from llm_code.api.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=base_url,
            api_key=api_key,
            model_name=model,
            timeout=timeout,
            max_retries=max_retries,
            native_tools=native_tools,
        )

    @staticmethod
    def _make_anthropic(
        model: str,
        api_key: str,
        timeout: float,
        max_retries: int,
    ) -> LLMProvider:
        from llm_code.api.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key,
            model_name=model,
            timeout=timeout,
            max_retries=max_retries,
        )


# ---------------------------------------------------------------------------
# M5 streaming facade — thin helpers around the provider's native async API
# ---------------------------------------------------------------------------


async def stream_async(
    provider: LLMProvider,
    request: MessageRequest,
) -> AsyncIterator[StreamEvent]:
    """Yield :class:`StreamEvent` from ``provider.stream_message``.

    Exists so callers can import a single ``stream_async`` symbol
    without reaching into specific provider classes. The underlying
    provider method is already an async iterator — we simply forward
    events through, keeping backpressure end-to-end.
    """
    async for event in await _ensure_async_iter(provider.stream_message(request)):
        yield event


async def _ensure_async_iter(maybe_coro):
    """Normalise ``provider.stream_message(request)`` to an async iterator.

    Providers vary: some return an ``AsyncIterator`` directly, others
    return a coroutine that resolves to one. This helper accepts either
    shape so callers never have to branch.
    """
    if asyncio.iscoroutine(maybe_coro):
        return await maybe_coro
    return maybe_coro


def stream(
    provider: LLMProvider,
    request: MessageRequest,
) -> Iterator[StreamEvent]:
    """Sync facade around :func:`stream_async`.

    Pumps the async iterator on a daemon thread and yields items via
    a ``queue.Queue``. Exists for legacy callers; in-loop callers must
    use :func:`stream_async` — calling this from inside a running loop
    raises :class:`RuntimeError` because it would block the loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "stream() cannot be called from inside a running event loop — "
            "use `async for ev in stream_async(provider, req)` instead."
        )
    q: "queue.Queue[object]" = queue.Queue()
    _SENTINEL = object()

    async def _pump() -> None:
        try:
            async for event in stream_async(provider, request):
                q.put(event)
        except BaseException as exc:  # forward errors through the queue
            q.put(exc)
        finally:
            q.put(_SENTINEL)

    def _run() -> None:
        asyncio.run(_pump())

    threading.Thread(target=_run, daemon=True, name="llmcode-stream-bridge").start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            return
        if isinstance(item, BaseException):
            raise item
        yield item  # type: ignore[misc]

"""Runtime adapter for formal server sessions."""
from __future__ import annotations

import copy
import time
from typing import Any

from llm_code.api.types import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolProgress,
)


class ServerRuntimeBridge:
    """Expose a ``ConversationRuntime`` through ``send_user_message``."""

    def __init__(self, runtime: Any, manager: Any, session_id: str) -> None:
        self._runtime = runtime
        self._manager = manager
        self._session_id = session_id

    def fork_for_session(self, session_id: str) -> "ServerRuntimeBridge":
        return ServerRuntimeBridge(
            runtime=copy.deepcopy(self._runtime),
            manager=self._manager,
            session_id=session_id,
        )

    async def send_user_message(self, text: str) -> None:
        start = time.monotonic()
        output_tokens = 0
        await self._emit({"type": "thinking_start"})
        try:
            async for event in self._runtime.run_turn(text):
                if isinstance(event, StreamTextDelta):
                    await self._emit({"type": "text_delta", "text": event.text})
                elif isinstance(event, StreamToolExecStart):
                    await self._emit({
                        "type": "tool_start",
                        "name": event.tool_name,
                        "detail": event.args_summary,
                    })
                elif isinstance(event, StreamToolExecResult):
                    await self._emit({
                        "type": "tool_result",
                        "name": event.tool_name,
                        "output": event.output[:500],
                        "isError": event.is_error,
                    })
                elif isinstance(event, StreamToolProgress):
                    await self._emit({
                        "type": "tool_progress",
                        "name": event.tool_name,
                        "message": event.message,
                    })
                elif isinstance(event, StreamMessageStop):
                    if event.usage and event.usage.output_tokens > 0:
                        output_tokens = event.usage.output_tokens
            await self._emit({"type": "text_done", "text": ""})
            await self._emit({
                "type": "thinking_stop",
                "elapsed": time.monotonic() - start,
                "tokens": output_tokens,
            })
        except Exception as exc:  # noqa: BLE001
            await self._emit({"type": "error", "message": str(exc)})
        finally:
            await self._emit({"type": "turn_done"})

    async def _emit(self, payload: dict) -> None:
        await self._manager.emit_event(self._session_id, payload)

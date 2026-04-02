"""Core agentic conversation runtime: turn loop with streaming and tool execution."""
from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any, AsyncIterator

from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.permissions import PermissionOutcome
from llm_code.tools.parsing import ParsedToolCall, parse_tool_calls

if TYPE_CHECKING:
    from llm_code.runtime.context import ProjectContext
    from llm_code.runtime.permissions import PermissionPolicy
    from llm_code.runtime.prompt import SystemPromptBuilder
    from llm_code.runtime.session import Session
    from llm_code.tools.registry import ToolRegistry


@dataclasses.dataclass(frozen=True)
class TurnSummary:
    iterations: int
    total_usage: TokenUsage


# ---------------------------------------------------------------------------
# ConversationRuntime
# ---------------------------------------------------------------------------

class ConversationRuntime:
    """Agentic loop that drives LLM turns, tool execution, and session updates."""

    def __init__(
        self,
        provider: Any,
        tool_registry: "ToolRegistry",
        permission_policy: "PermissionPolicy",
        hook_runner: Any,
        prompt_builder: "SystemPromptBuilder",
        config: Any,
        session: "Session",
        context: "ProjectContext",
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._permissions = permission_policy
        self._hooks = hook_runner
        self._prompt_builder = prompt_builder
        self._config = config
        self.session = session
        self._context = context

    async def run_turn(self, user_input: str) -> AsyncIterator[StreamEvent]:
        """Run one user turn (may involve multiple LLM calls for tool use)."""
        # 1. Add user message to session
        user_msg = Message(role="user", content=(TextBlock(text=user_input),))
        self.session = self.session.add_message(user_msg)

        accumulated_usage = TokenUsage(input_tokens=0, output_tokens=0)

        for _iteration in range(self._config.max_turn_iterations):
            # 2. Build system prompt
            use_native = getattr(self._provider, "supports_native_tools", lambda: True)()
            tool_defs = self._tool_registry.definitions()
            system_prompt = self._prompt_builder.build(
                self._context,
                tools=tool_defs,
                native_tools=use_native,
            )

            # 3. Create request and stream
            request = MessageRequest(
                model=getattr(self._config, "model", ""),
                messages=self.session.messages,
                system=system_prompt,
                tools=tool_defs if use_native else (),
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

            stream = await self._provider.stream_message(request)

            # 4. Collect events and buffers
            text_parts: list[str] = []
            native_tool_calls: list[dict] = {}  # id -> {id, name, json_parts}
            native_tool_list: list[dict] = []
            stop_event: StreamMessageStop | None = None

            async for event in stream:
                # Yield streaming events to caller
                yield event

                if isinstance(event, StreamTextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, StreamToolUseStart):
                    native_tool_calls[event.id] = {
                        "id": event.id,
                        "name": event.name,
                        "json_parts": [],
                    }
                elif isinstance(event, StreamToolUseInputDelta):
                    if event.id in native_tool_calls:
                        native_tool_calls[event.id]["json_parts"].append(event.partial_json)
                elif isinstance(event, StreamMessageStop):
                    stop_event = event

            # Accumulate usage
            if stop_event:
                accumulated_usage = TokenUsage(
                    input_tokens=accumulated_usage.input_tokens + stop_event.usage.input_tokens,
                    output_tokens=accumulated_usage.output_tokens + stop_event.usage.output_tokens,
                )

            # Build native tool call list for parsing
            for call_data in native_tool_calls.values():
                raw_json = "".join(call_data["json_parts"])
                try:
                    parsed_input = json.loads(raw_json) if raw_json else {}
                except json.JSONDecodeError:
                    parsed_input = {}
                native_tool_list.append({
                    "id": call_data["id"],
                    "name": call_data["name"],
                    "input": parsed_input,
                })

            # 5. Parse tool calls (dual-track)
            response_text = "".join(text_parts)
            parsed_calls = parse_tool_calls(
                response_text=response_text,
                native_tool_calls=native_tool_list if native_tool_list else None,
            )

            # 6. Build assistant message content
            assistant_blocks: list = []
            if response_text:
                assistant_blocks.append(TextBlock(text=response_text))
            for call in parsed_calls:
                assistant_blocks.append(
                    ToolUseBlock(id=call.id, name=call.name, input=call.args)
                )

            # 7. Add assistant message to session
            if assistant_blocks:
                assistant_msg = Message(
                    role="assistant",
                    content=tuple(assistant_blocks),
                )
                self.session = self.session.add_message(assistant_msg)

            # 8. If no tool calls → end turn
            if not parsed_calls:
                break

            # 9. Execute tools and collect results
            tool_result_blocks: list[ToolResultBlock] = []
            for call in parsed_calls:
                result_block = await self._execute_tool(call)
                tool_result_blocks.append(result_block)

            # Add tool results as user message
            if tool_result_blocks:
                tool_result_msg = Message(
                    role="user",
                    content=tuple(tool_result_blocks),
                )
                self.session = self.session.add_message(tool_result_msg)

            # 10. Loop back for LLM to process results

        # Update session usage
        self.session = self.session.update_usage(accumulated_usage)

    async def _execute_tool(self, call: ParsedToolCall) -> ToolResultBlock:
        """Authorize, run hooks, execute tool, return ToolResultBlock."""
        tool = self._tool_registry.get(call.name)
        required_level = tool.required_permission if tool else None

        from llm_code.tools.base import PermissionLevel
        if required_level is None:
            required_level = PermissionLevel.READ_ONLY

        outcome = self._permissions.authorize(call.name, required_level)

        if outcome == PermissionOutcome.DENY:
            return ToolResultBlock(
                tool_use_id=call.id,
                content=f"Permission denied for tool '{call.name}'",
                is_error=True,
            )

        if outcome == PermissionOutcome.NEED_PROMPT:
            # In this implementation, NEED_PROMPT without a UI → deny
            return ToolResultBlock(
                tool_use_id=call.id,
                content=f"Tool '{call.name}' requires user approval (not available in this context)",
                is_error=True,
            )

        # Pre-tool hook
        args = call.args
        hook_runner = self._hooks
        if hasattr(hook_runner, "pre_tool_use"):
            hook_result = hook_runner.pre_tool_use(call.name, args)
            # Support both sync HookOutcome and async/dict returns
            if hasattr(hook_result, "__await__"):
                hook_result = await hook_result
            # If it's a HookOutcome with denied=True, deny the tool
            if hasattr(hook_result, "denied") and hook_result.denied:
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Tool '{call.name}' blocked by hook",
                    is_error=True,
                )
            # If it returned modified args (dict), use those
            if isinstance(hook_result, dict):
                args = hook_result

        # Execute the tool
        tool_result = self._tool_registry.execute(call.name, args)

        # Post-tool hook
        if hasattr(hook_runner, "post_tool_use"):
            post_result = hook_runner.post_tool_use(call.name, args, tool_result)
            if hasattr(post_result, "__await__"):
                await post_result

        return ToolResultBlock(
            tool_use_id=call.id,
            content=tool_result.output,
            is_error=tool_result.is_error,
        )

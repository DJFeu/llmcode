"""Extracted tool execution pipeline from ConversationRuntime."""
from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import ValidationError

from llm_code.logging import get_logger
from llm_code.api.types import (
    StreamEvent,
    StreamPermissionRequest,
    StreamToolExecResult,
    StreamToolExecStart,
    StreamToolProgress,
    ToolResultBlock,
)
from llm_code.runtime.permission_denial_tracker import (
    DenialSource,
    PermissionDenialTracker,
)
from llm_code.runtime.permissions import PermissionOutcome
from llm_code.tools.base import PermissionLevel, ToolResult

if TYPE_CHECKING:
    from llm_code.runtime.conversation import ConversationRuntime
    from llm_code.tools.parsing import ParsedToolCall

logger = get_logger(__name__)

# Thread pool for running blocking tool execution off the event loop
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# Maximum number of characters to inline in tool results
_MAX_INLINE_RESULT = 2000


def _merge_hook_extra_output(result: ToolResult, outcome: Any) -> ToolResult:
    """Append HookOutcome.extra_output to a ToolResult.output (immutable update)."""
    extra = getattr(outcome, "extra_output", "") or ""
    if not extra:
        return result
    return ToolResult(
        output=result.output + extra,
        is_error=result.is_error,
        metadata=result.metadata,
    )


def _tool_capability_labels(tool: Any) -> tuple[str, ...]:
    """Return a sorted tuple of capability labels the tool satisfies.

    Feeds :class:`StreamToolExecStart.tool_capabilities` (H10 deep wire)
    so TUIs / telemetry / audit logs can branch on "this call is
    destructive" or "this call needs network" without re-inspecting
    the tool object. ``isinstance`` is cheap — the runtime-checkable
    Protocols only verify attribute presence.
    """
    from llm_code.tools.capabilities import (
        DestructiveCapability,
        NetworkCapability,
        ReadOnlyCapability,
        RollbackableCapability,
    )

    labels: list[str] = []
    if isinstance(tool, ReadOnlyCapability):
        labels.append("read_only")
    if isinstance(tool, DestructiveCapability):
        labels.append("destructive")
    if isinstance(tool, RollbackableCapability):
        labels.append("rollbackable")
    if isinstance(tool, NetworkCapability):
        labels.append("network")
    labels.sort()
    return tuple(labels)


def _record_denial(
    runtime: Any,
    *,
    tool_name: str,
    tool_use_id: str,
    input: dict,
    reason: str,
    source: DenialSource,
) -> None:
    """Record a denied tool call on the runtime's denial tracker.

    Lazily initialises ``runtime._permission_denial_tracker`` — mirrors the
    C4 pattern for ``_auto_compact_state`` so ``ConversationRuntime.__init__``
    doesn't need to change. Swallows any tracker-side error because deny
    branches are already on the failure path and we must not mask the
    original permission outcome.
    """
    try:
        tracker = getattr(runtime, "_permission_denial_tracker", None)
        if tracker is None:
            tracker = PermissionDenialTracker()
            runtime._permission_denial_tracker = tracker
        tracker.record(
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            input=input,
            reason=reason,
            source=source,
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("_record_denial swallowed tracker error", exc_info=True)


class ToolExecutionPipeline:
    """Validate, permission-check, execute, and post-process a single tool call.

    Extracted from :class:`ConversationRuntime` to isolate the ~400-line
    tool dispatch path into a focused, testable module.  Keeps a
    back-reference to the runtime so it can access the registry, hooks,
    config, session, permissions, and other collaborators.
    """

    def __init__(self, runtime: "ConversationRuntime") -> None:
        self._runtime = runtime

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_with_streaming(
        self, call: "ParsedToolCall",
    ) -> AsyncIterator[StreamEvent | ToolResultBlock]:
        """Validate -> safety -> permission -> run in thread -> yield progress + result."""
        rt = self._runtime
        logger.debug("Executing tool: %s", call.name)

        # 1. Look up tool
        tool = rt._tool_registry.get(call.name)
        if tool is None:
            logger.warning("Unknown tool requested: %s", call.name)
            rt._fire_hook("tool_error", {"tool_name": call.name, "error": "unknown tool"})
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Unknown tool '{call.name}'",
                is_error=True,
            )
            return

        # 1b. Defense-in-depth role check
        _subagent_role = getattr(rt, "_subagent_role", None)
        if _subagent_role is not None:
            from llm_code.tools.agent_roles import is_tool_allowed_for_role

            if not is_tool_allowed_for_role(_subagent_role, call.name):
                logger.warning(
                    "Tool %s blocked by role %s",
                    call.name,
                    _subagent_role.name,
                )
                rt._fire_hook("tool_denied", {"tool_name": call.name})
                _record_denial(
                    rt,
                    tool_name=call.name,
                    tool_use_id=call.id,
                    input=call.args,
                    reason=f"role {_subagent_role.name} cannot use tool {call.name}",
                    source=DenialSource.POLICY,
                )
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=(
                        f"Tool '{call.name}' is not permitted for role "
                        f"'{_subagent_role.name}'"
                    ),
                    is_error=True,
                )
                return

        # 2. Validate input
        try:
            validated_args = tool.validate_input(call.args)
        except ValidationError as exc:
            errors = exc.errors()
            fields = ", ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in errors
            )
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Invalid input for tool '{call.name}': {fields}",
                is_error=True,
            )
            return

        # 3. Safety analysis -> effective permission level
        if hasattr(tool, "classify") and callable(tool.classify):
            safety = tool.classify(validated_args)
            if safety.is_blocked:
                rt._fire_hook("tool_denied", {"tool_name": call.name})
                _record_denial(
                    rt,
                    tool_name=call.name,
                    tool_use_id=call.id,
                    input=validated_args,
                    reason=f"safety classifier blocked: {'; '.join(safety.reasons)}",
                    source=DenialSource.POLICY,
                )
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Dangerous command blocked: {'; '.join(safety.reasons)}",
                    is_error=True,
                )
                return

        if tool.is_read_only(validated_args):
            effective = PermissionLevel.READ_ONLY
        elif tool.is_destructive(validated_args):
            effective = PermissionLevel.FULL_ACCESS
        else:
            effective = tool.required_permission

        # 4a. Plan mode -- deny write tools (via harness)
        denial_msg = rt._harness.check_pre_tool(call.name)
        if denial_msg:
            rt._fire_hook("tool_denied", {"tool_name": call.name})
            _record_denial(
                rt,
                tool_name=call.name,
                tool_use_id=call.id,
                input=validated_args,
                reason=f"plan mode: {denial_msg}",
                source=DenialSource.POLICY,
            )
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=denial_msg,
                is_error=True,
            )
            return

        # 4. Permission check
        outcome = rt._permissions.authorize(
            call.name,
            tool.required_permission,
            effective_level=effective,
        )

        if outcome == PermissionOutcome.DENY:
            rt._fire_hook("tool_denied", {"tool_name": call.name})
            _record_denial(
                rt,
                tool_name=call.name,
                tool_use_id=call.id,
                input=validated_args,
                reason=(
                    f"permission policy denied (required={tool.required_permission.name}, "
                    f"effective={effective.name})"
                ),
                source=DenialSource.POLICY,
            )
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Permission denied for tool '{call.name}'",
                is_error=True,
            )
            return

        if outcome == PermissionOutcome.NEED_PROMPT:
            args_preview = json.dumps(validated_args, default=str)[:120]

            if not rt.is_session_allowed(call.name, args_preview, validated_args):
                # Speculative pre-execution
                spec_executor = None
                try:
                    from llm_code.runtime.speculative import SpeculativeExecutor
                    import uuid as _uuid
                    session_id = f"{call.name}-{_uuid.uuid4().hex[:8]}"
                    spec_executor = SpeculativeExecutor(
                        tool=tool,
                        args=validated_args,
                        base_dir=rt._context.cwd,
                        session_id=session_id,
                    )
                    spec_executor.pre_execute()
                except Exception:
                    spec_executor = None

                spec_diff_lines: tuple[str, ...] = ()
                spec_pending_files: tuple[str, ...] = ()
                if spec_executor is not None:
                    try:
                        spec_pending_files = tuple(
                            str(p) for p in spec_executor.list_pending_changes()
                        )
                    except Exception:
                        spec_pending_files = ()
                    try:
                        result_obj = spec_executor._result
                        if result_obj is not None and result_obj.metadata:
                            hunks = result_obj.metadata.get("diff") or []
                            collected: list[str] = []
                            for hunk in hunks:
                                old_start = hunk.get("old_start", 0)
                                old_lines = hunk.get("old_lines", 0)
                                new_start = hunk.get("new_start", 0)
                                new_lines = hunk.get("new_lines", 0)
                                collected.append(
                                    f"@@ -{old_start},{old_lines} "
                                    f"+{new_start},{new_lines} @@"
                                )
                                for line in hunk.get("lines", []):
                                    collected.append(line)
                            spec_diff_lines = tuple(collected)
                    except Exception:
                        spec_diff_lines = ()

                yield StreamPermissionRequest(
                    tool_name=call.name,
                    args_preview=args_preview,
                    diff_lines=spec_diff_lines,
                    pending_files=spec_pending_files,
                )

                loop = asyncio.get_running_loop()
                rt._perm_mgr._permission_future = loop.create_future()
                try:
                    response = await asyncio.wait_for(rt._perm_mgr._permission_future, timeout=300)
                except asyncio.TimeoutError:
                    response = "deny"
                    logger.warning("Permission prompt for '%s' timed out (300s), auto-denying", call.name)
                finally:
                    rt._perm_mgr._permission_future = None

                if response.startswith("edit:"):
                    try:
                        edited = json.loads(response[5:])
                        if isinstance(edited, dict):
                            validated_args = edited
                    except (json.JSONDecodeError, ValueError):
                        pass
                    response = "allow"

                if response in ("allow", "always", "always_kind", "always_exact"):
                    if response in ("always", "always_kind"):
                        if hasattr(rt._permissions, "allow_tool"):
                            rt._permissions.allow_tool(call.name)
                        rt.record_permission_choice(
                            "always_kind", call.name, args_preview, validated_args,
                        )
                    elif response == "always_exact":
                        rt.record_permission_choice(
                            "always_exact", call.name, args_preview, validated_args,
                        )
                    if spec_executor is not None:
                        try:
                            spec_executor.confirm()
                        except Exception:
                            pass
                else:
                    if spec_executor is not None:
                        try:
                            spec_executor.deny()
                        except Exception:
                            pass
                    rt._fire_hook("tool_denied", {"tool_name": call.name})
                    _record_denial(
                        rt,
                        tool_name=call.name,
                        tool_use_id=call.id,
                        input=validated_args,
                        reason=f"user rejected interactive prompt (response={response!r})",
                        source=DenialSource.USER,
                    )
                    yield ToolResultBlock(
                        tool_use_id=call.id,
                        content=f"Tool '{call.name}' denied by user",
                        is_error=True,
                    )
                    return

        # 4b. Create checkpoint before mutating tools
        if rt._checkpoint_mgr is not None and not tool.is_read_only(validated_args):
            try:
                rt._checkpoint_mgr.create(call.name, validated_args)
            except Exception:
                pass

        # 5. Pre-tool hook
        args = validated_args
        hook_runner = rt._hooks
        if hasattr(hook_runner, "pre_tool_use"):
            hook_result = hook_runner.pre_tool_use(call.name, args)
            if hasattr(hook_result, "__await__"):
                hook_result = await hook_result
            if hasattr(hook_result, "denied") and hook_result.denied:
                _hook_reason = (
                    "; ".join(getattr(hook_result, "messages", ()) or ())
                    or "pre_tool_use hook denied"
                )
                _record_denial(
                    rt,
                    tool_name=call.name,
                    tool_use_id=call.id,
                    input=args,
                    reason=_hook_reason,
                    source=DenialSource.HOOK,
                )
                yield ToolResultBlock(
                    tool_use_id=call.id,
                    content=f"Tool '{call.name}' blocked by hook",
                    is_error=True,
                )
                return
            if isinstance(hook_result, dict):
                args = hook_result

        # 6. Emit tool execution start event
        args_preview = repr(args)
        if rt._vcr_recorder is not None:
            rt._vcr_recorder.record("tool_call", {"name": call.name, "args": args_preview})
        yield StreamToolExecStart(
            tool_name=call.name,
            args_summary=args_preview,
            tool_id=call.id,
            tool_capabilities=_tool_capability_labels(tool),
        )
        _tool_start = time.monotonic()

        # 7. Execute in thread pool with asyncio.Queue progress bridge
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(p):
            loop.call_soon_threadsafe(queue.put_nowait, p)

        def run_tool():
            result = tool.execute_with_progress(args, on_progress)
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel
            return result

        future = loop.run_in_executor(_TOOL_EXECUTOR, run_tool)

        try:
            while True:
                progress = await queue.get()
                if progress is None:
                    break
                yield StreamToolProgress(
                    tool_name=progress.tool_name,
                    message=progress.message,
                    percent=progress.percent,
                )

            tool_result = await future
        except asyncio.CancelledError:
            rt._fire_hook(
                "tool_cancelled",
                {"tool_name": call.name, "tool_id": call.id},
            )
            logger.warning(
                "tool %s (id=%s) cancelled mid-execution; background "
                "thread will continue but runtime records an error "
                "ToolResultBlock so the session stays consistent",
                call.name, call.id,
            )
            yield ToolResultBlock(
                tool_use_id=call.id,
                content=f"Tool '{call.name}' execution was cancelled.",
                is_error=True,
            )
            raise
        tool_result = self.budget_result(tool_result, call.id)

        # Parse permission denials for user guidance
        if tool_result.is_error and tool_result.output and call.name == "bash":
            try:
                from llm_code.runtime.denial_parser import parse_denial, format_denial_hint
                denial = parse_denial(tool_result.output)
                if denial:
                    tool_result = ToolResult(
                        output=tool_result.output + format_denial_hint(denial),
                        is_error=True,
                        metadata=tool_result.metadata,
                    )
            except Exception:
                pass

        _tool_duration_ms = (time.monotonic() - _tool_start) * 1000
        rt._telemetry.trace_tool(
            tool_name=call.name,
            duration_ms=_tool_duration_ms,
            is_error=tool_result.is_error,
        )

        # 7. Post-tool hook
        if hasattr(hook_runner, "post_tool_use"):
            post_result = hook_runner.post_tool_use(call.name, args, tool_result)
            if hasattr(post_result, "__await__"):
                post_result = await post_result
            tool_result = _merge_hook_extra_output(tool_result, post_result)
        if hasattr(hook_runner, "fire_python"):
            inproc = hook_runner.fire_python(
                "post_tool_use",
                {
                    "tool_name": call.name,
                    "tool_input": args,
                    "tool_output": tool_result.output,
                    "file_path": args.get("file_path") or args.get("path", ""),
                    "session_id": getattr(rt._context, "session_id", ""),
                    "tokens_used": getattr(rt, "_last_input_tokens", 0),
                    "tokens_max": getattr(rt, "_max_input_tokens", 0),
                },
            )
            tool_result = _merge_hook_extra_output(tool_result, inproc)

        # 7b. Run harness sensors
        try:
            findings = await rt._harness.post_tool(
                tool_name=call.name,
                file_path=args.get("file_path") or args.get("path", ""),
                is_error=tool_result.is_error,
            )
            for finding in findings:
                if finding.severity == "error":
                    yield StreamToolProgress(
                        tool_name=finding.sensor,
                        message=f"{finding.sensor} found issues in {Path(finding.file_path).name}:\n{finding.message}",
                        percent=None,
                    )
        except Exception:
            pass

        # 7c. Track file accesses for post-compact restoration
        if call.name in ("read_file", "write_file", "edit_file") and not tool_result.is_error:
            _path = call.args.get("path", "") or call.args.get("file_path", "")
            if _path:
                rt._compressor.record_file_access(_path)

        # 8. Emit tool execution result event
        if rt._vcr_recorder is not None:
            rt._vcr_recorder.record("tool_result", {
                "name": call.name,
                "output": tool_result.output[:200],
                "is_error": tool_result.is_error,
            })
        yield StreamToolExecResult(
            tool_name=call.name,
            output=tool_result.output[:200],
            is_error=tool_result.is_error,
            metadata=tool_result.metadata,
            tool_id=call.id,
        )

        yield ToolResultBlock(
            tool_use_id=call.id,
            content=tool_result.output,
            is_error=tool_result.is_error,
        )

    def budget_result(self, result: ToolResult, call_id: str) -> ToolResult:
        """If result is too large, persist to disk and return truncated summary."""
        if len(result.output) <= _MAX_INLINE_RESULT:
            return result

        rt = self._runtime
        cache_dir = rt._context.cwd / ".llmcode" / "result_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{call_id}.txt"
        cache_path.write_text(result.output, encoding="utf-8")

        summary = (
            result.output[:1000]
            + f"\n\n... [{len(result.output)} chars total, full output saved to {cache_path}. Use read_file to access.]"
        )
        return ToolResult(output=summary, is_error=result.is_error, metadata=result.metadata)

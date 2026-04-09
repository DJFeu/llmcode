"""PlanModeTool — lets the model explicitly switch between plan and act modes.

When plan mode is active, write/destructive tools are blocked. The model
can call ``exit_plan_mode`` to signal that planning is complete and it's
ready to execute. This is more structured than the user toggling ``/plan``
because the model takes responsibility for the transition.
"""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class ExitPlanModeInput(BaseModel):
    reason: str = ""


class ExitPlanModeTool(Tool):
    """Signal that planning is complete and switch to execution mode.

    The model calls this tool when it has finished planning and is
    ready to start making changes. The runtime disables plan mode
    so write tools (edit_file, write_file, bash, etc.) become available.
    """

    def __init__(self, runtime: object | None = None) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "exit_plan_mode"

    @property
    def description(self) -> str:
        return (
            "Signal that planning is complete and switch to execution mode. "
            "Call this when you have a clear plan and are ready to start "
            "making changes. Write tools will become available after this call."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief summary of the plan (optional)",
                },
            },
            "required": [],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[ExitPlanModeInput]:
        return ExitPlanModeInput

    def execute(self, args: dict) -> ToolResult:
        reason = args.get("reason", "")
        if self._runtime is not None:
            if hasattr(self._runtime, "plan_mode"):
                if not self._runtime.plan_mode:
                    return ToolResult(output="Already in execution mode.")
                self._runtime.plan_mode = False
                msg = "Switched to execution mode."
                if reason:
                    msg += f" Plan: {reason}"
                return ToolResult(output=msg)
        return ToolResult(output="Plan mode transition noted.", metadata={"plan_mode": False})


class EnterPlanModeInput(BaseModel):
    reason: str = ""


class EnterPlanModeTool(Tool):
    """Switch to plan mode for read-only analysis before making changes."""

    def __init__(self, runtime: object | None = None) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "enter_plan_mode"

    @property
    def description(self) -> str:
        return (
            "Switch to plan mode for read-only analysis. "
            "Write tools will be blocked until exit_plan_mode is called."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why planning is needed",
                },
            },
            "required": [],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    @property
    def input_model(self) -> type[EnterPlanModeInput]:
        return EnterPlanModeInput

    def execute(self, args: dict) -> ToolResult:
        reason = args.get("reason", "")
        if self._runtime is not None:
            if hasattr(self._runtime, "plan_mode"):
                if self._runtime.plan_mode:
                    return ToolResult(output="Already in plan mode.")
                self._runtime.plan_mode = True
                msg = "Switched to plan mode. Write tools are now blocked."
                if reason:
                    msg += f" Reason: {reason}"
                return ToolResult(output=msg)
        return ToolResult(output="Plan mode transition noted.", metadata={"plan_mode": True})

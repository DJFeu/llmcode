"""TaskPlanTool: create a task with title, plan, and goals."""
from __future__ import annotations

from pydantic import BaseModel

from llm_code.task.manager import TaskLifecycleManager
from llm_code.tools.base import PermissionLevel, Tool, ToolResult


class TaskPlanInput(BaseModel):
    title: str
    plan: str = ""
    goals: list[str] = []


class TaskPlanTool(Tool):
    """Create a new structured task with a plan and goals."""

    def __init__(self, manager: TaskLifecycleManager, session_id: str = "") -> None:
        self._manager = manager
        self._session_id = session_id

    @property
    def name(self) -> str:
        return "task_plan"

    @property
    def description(self) -> str:
        return (
            "Create a new structured task. Provide a title, an implementation plan, "
            "and measurable goals. The task starts in PLAN status."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "plan": {"type": "string", "description": "Step-by-step implementation plan"},
                "goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Measurable completion goals",
                },
            },
            "required": ["title"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    @property
    def input_model(self) -> type[TaskPlanInput]:
        return TaskPlanInput

    def execute(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        if not title:
            return ToolResult(output="Error: title is required", is_error=True)

        plan = args.get("plan", "")
        goals = tuple(args.get("goals", []))

        task = self._manager.create_task(
            title=title,
            plan=plan,
            goals=goals,
            session_id=self._session_id,
        )
        return ToolResult(
            output=(
                f"Created task {task.id}: {task.title}\n"
                f"Status: {task.status.value}\n"
                f"Goals: {', '.join(task.goals) if task.goals else '(none)'}\n"
                f"Plan:\n{task.plan or '(no plan set)'}"
            )
        )

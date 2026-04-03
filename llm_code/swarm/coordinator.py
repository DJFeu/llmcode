"""Coordinator — auto-decompose tasks and dispatch to swarm workers."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from llm_code.api.provider import LLMProvider
from llm_code.api.types import Message, MessageRequest, TextBlock
from llm_code.swarm.manager import SwarmManager

logger = logging.getLogger(__name__)

_DECOMPOSE_PROMPT = """\
Break the following task into independent subtasks that can be executed in parallel by specialized agents.

Output ONLY a JSON array (no explanation, no markdown fences). Each element must have:
  - "role": a short role label (e.g. "coder", "tester", "reviewer", "researcher")
  - "task": a clear, self-contained task description

Example output:
[
  {{"role": "coder", "task": "Implement the binary search function in utils.py"}},
  {{"role": "tester", "task": "Write unit tests for the binary search function"}}
]

Task to decompose:
{task}
"""

_AGGREGATE_PROMPT = """\
You are a coordinator agent summarizing the results of parallel worker agents.

Original task: {original_task}

Worker results:
{results}

Provide a concise summary of what was accomplished, any issues encountered, and the combined outcome.
"""


class Coordinator:
    """Orchestrate task decomposition and parallel worker dispatch.

    Sends the original task to the LLM for decomposition into subtasks,
    creates swarm members per subtask, monitors completion via the mailbox,
    then aggregates results with a final LLM summary.
    """

    POLL_INTERVAL: float = 5.0
    TIMEOUT: float = 300.0
    COORDINATOR_ID: str = "coordinator"

    def __init__(
        self,
        manager: SwarmManager,
        provider: LLMProvider,
        config: Any,
    ) -> None:
        self._manager = manager
        self._provider = provider
        self._config = config

    async def orchestrate(self, task: str) -> str:
        """Decompose task, dispatch workers, wait for completion, return summary.

        Args:
            task: High-level task description to decompose and delegate.

        Returns:
            Aggregated summary string from all worker results.
        """
        subtasks = await self._decompose(task)
        if not subtasks:
            return f"No subtasks generated for: {task}"

        max_members = getattr(
            getattr(self._config, "swarm", None), "max_members", 5
        )
        subtasks = subtasks[:max_members]

        members = []
        for subtask in subtasks:
            role = subtask.get("role", "worker")
            subtask_desc = subtask.get("task", "")
            if not subtask_desc:
                continue
            try:
                member = await self._manager.create_member(role=role, task=subtask_desc)
                members.append(member)
                logger.info("Spawned swarm member %s (%s)", member.id, role)
            except ValueError as exc:
                logger.warning("Could not create member for role=%s: %s", role, exc)
                break

        if not members:
            return "Failed to create any swarm members."

        results = await self._wait_for_completion(
            member_ids=[m.id for m in members],
            timeout=self.TIMEOUT,
            poll_interval=self.POLL_INTERVAL,
        )

        summary = await self._aggregate(task, members, results)
        return summary

    async def _decompose(self, task: str) -> list[dict]:
        """Ask the LLM to decompose task into subtasks. Returns list of dicts."""
        prompt = _DECOMPOSE_PROMPT.format(task=task)
        model = getattr(self._config, "model", None) or "default"
        request = MessageRequest(
            model=model,
            messages=(
                Message(
                    role="user",
                    content=(TextBlock(text=prompt),),
                ),
            ),
            max_tokens=1024,
            stream=False,
        )
        try:
            response = await self._provider.send_message(request)
            text = ""
            for block in response.content:
                if isinstance(block, TextBlock):
                    text += block.text
            return self._parse_json_list(text)
        except Exception as exc:
            logger.error("Decomposition failed: %s", exc)
            return []

    def _parse_json_list(self, text: str) -> list[dict]:
        """Extract a JSON array from LLM output (strips markdown fences)."""
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
        # Find first '[' and last ']'
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            logger.warning("Could not find JSON array in decomposition output: %r", text[:200])
            return []
        try:
            data = json.loads(cleaned[start : end + 1])
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            return []
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse error in decomposition: %s", exc)
            return []

    async def _wait_for_completion(
        self,
        member_ids: list[str],
        timeout: float,
        poll_interval: float,
    ) -> dict[str, list[str]]:
        """Poll the mailbox until all members report completion or timeout.

        Each member is expected to send a message to COORDINATOR_ID containing
        "DONE" or "COMPLETE" when finished.

        Returns:
            Mapping of member_id -> list of message texts received.
        """
        results: dict[str, list[str]] = {mid: [] for mid in member_ids}
        completed: set[str] = set()
        elapsed = 0.0

        while len(completed) < len(member_ids) and elapsed < timeout:
            for mid in member_ids:
                if mid in completed:
                    continue
                msgs = self._manager.mailbox.receive_and_clear(
                    from_id=mid, to_id=self.COORDINATOR_ID
                )
                for msg in msgs:
                    results[mid].append(msg.text)
                    upper = msg.text.upper()
                    if "DONE" in upper or "COMPLETE" in upper or "FINISHED" in upper:
                        completed.add(mid)
                        logger.info("Member %s reported completion", mid)

            if len(completed) < len(member_ids):
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

        if elapsed >= timeout:
            logger.warning(
                "Coordinator timed out waiting for members: %s",
                [mid for mid in member_ids if mid not in completed],
            )

        return results

    async def _aggregate(self, original_task: str, members: list, results: dict[str, list[str]]) -> str:
        """Ask the LLM to aggregate all worker results into a summary."""
        result_lines = []
        for member in members:
            texts = results.get(member.id, [])
            result_lines.append(
                f"[{member.role}] {member.task}\n"
                + (("\n".join(texts)) if texts else "(no output received)")
            )

        results_text = "\n\n".join(result_lines)
        prompt = _AGGREGATE_PROMPT.format(
            original_task=original_task,
            results=results_text,
        )
        model = getattr(self._config, "model", None) or "default"
        request = MessageRequest(
            model=model,
            messages=(
                Message(
                    role="user",
                    content=(TextBlock(text=prompt),),
                ),
            ),
            max_tokens=2048,
            stream=False,
        )
        try:
            response = await self._provider.send_message(request)
            text = ""
            for block in response.content:
                if isinstance(block, TextBlock):
                    text += block.text
            return text.strip() or results_text
        except Exception as exc:
            logger.error("Aggregation LLM call failed: %s", exc)
            return results_text

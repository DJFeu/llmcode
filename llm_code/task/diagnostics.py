"""Diagnostics engine: analyze verification failures and recommend actions."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from llm_code.task.types import TaskState, VerifyResult


@dataclasses.dataclass(frozen=True)
class DiagnosticReport:
    task_id: str
    failed_checks: tuple[str, ...]
    recommendation: str  # "continue" | "replan" | "escalate"
    summary: str
    report_path: str


class DiagnosticsEngine:
    """Analyze verify failures and recommend next action."""

    def __init__(self, diagnostics_dir: Path) -> None:
        self._dir = diagnostics_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def analyze(self, task: TaskState, verify_result: VerifyResult) -> DiagnosticReport:
        """Analyze a VerifyResult and return a DiagnosticReport with recommendation."""
        if verify_result.all_passed:
            return DiagnosticReport(
                task_id=task.id,
                failed_checks=(),
                recommendation="continue",
                summary="All checks passed.",
                report_path="",
            )

        failed = tuple(c.check_name for c in verify_result.checks if not c.passed)
        total_checks = len(verify_result.checks)
        failed_count = len(failed)

        # Determine recommendation based on failure severity and history
        prior_failures = sum(
            1 for vr in task.verify_results if not vr.all_passed
        )

        if prior_failures >= 2:
            # Multiple prior failures -> escalate
            recommendation = "escalate"
            summary = (
                f"Task has failed verification {prior_failures + 1} times. "
                f"Current failures: {', '.join(failed)}. Recommend escalation."
            )
        elif failed_count == total_checks:
            # All checks failed -> escalate
            recommendation = "escalate"
            summary = (
                f"All {total_checks} checks failed ({', '.join(failed)}). "
                "Recommend escalation."
            )
        else:
            # Partial failure -> replan
            recommendation = "replan"
            summary = (
                f"{failed_count}/{total_checks} checks failed ({', '.join(failed)}). "
                "Recommend replanning the failing areas."
            )

        # Save report to disk
        report_data = {
            "task_id": task.id,
            "task_title": task.title,
            "failed_checks": list(failed),
            "recommendation": recommendation,
            "summary": summary,
            "check_details": [
                {"name": c.check_name, "passed": c.passed, "output": c.output}
                for c in verify_result.checks
            ],
            "prior_failure_count": prior_failures,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        report_path = self._dir / f"{task.id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
        report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        return DiagnosticReport(
            task_id=task.id,
            failed_checks=failed,
            recommendation=recommendation,
            summary=summary,
            report_path=str(report_path),
        )

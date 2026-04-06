"""HarnessEngine — orchestrates guides (feedforward) and sensors (feedback)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_code.harness.config import HarnessConfig, HarnessControl, HarnessFinding
from llm_code.harness.guides import (
    analysis_context_guide,
    plan_mode_denied_tools,
    repo_map_guide,
)
from llm_code.harness.sensors import (
    auto_commit_sensor,
    code_rules_sensor,
    lsp_diagnose_sensor,
)

_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


class HarnessEngine:
    """Central orchestrator for all quality controls."""

    def __init__(self, config: HarnessConfig, cwd: Path) -> None:
        self._config = config
        self._cwd = cwd
        self._overrides: dict[str, bool] = {}
        self.plan_mode: bool = False
        self.analysis_context: str | None = None
        self.lsp_manager: Any | None = None

    @property
    def config(self) -> HarnessConfig:
        return self._config

    def _is_enabled(self, ctrl: HarnessControl) -> bool:
        if ctrl.name in self._overrides:
            return self._overrides[ctrl.name]
        return ctrl.enabled

    def _controls_by(self, category: str, trigger: str) -> list[HarnessControl]:
        return [
            c for c in self._config.controls
            if c.category == category and c.trigger == trigger and self._is_enabled(c)
        ]

    def pre_turn(self) -> list[str]:
        """Run pre_turn guides. Returns strings to inject into system prompt."""
        injections: list[str] = []
        for ctrl in self._controls_by("guide", "pre_turn"):
            text = self._run_guide(ctrl)
            if text:
                injections.append(text)
        return injections

    def check_pre_tool(self, tool_name: str) -> str | None:
        """Check pre_tool guides (plan mode). Returns denial message or None."""
        for ctrl in self._controls_by("guide", "pre_tool"):
            if ctrl.name == "plan_mode":
                denied = plan_mode_denied_tools(self.plan_mode)
                if tool_name in denied:
                    return f"Plan mode: read-only. Tool '{tool_name}' denied. Use /plan to switch to Act mode."
        return None

    def _run_guide(self, ctrl: HarnessControl) -> str:
        if ctrl.name == "repo_map":
            return repo_map_guide(cwd=self._cwd)
        if ctrl.name == "analysis_context":
            return analysis_context_guide(context=self.analysis_context)
        if ctrl.name == "architecture_doc":
            doc_path = self._cwd / ".llm-code" / "architecture.md"
            if doc_path.exists():
                try:
                    return doc_path.read_text(encoding="utf-8")
                except OSError:
                    return ""
            return ""
        if ctrl.name == "knowledge":
            from llm_code.harness.guides import knowledge_guide
            return knowledge_guide(cwd=self._cwd)
        return ""

    async def post_tool(
        self, tool_name: str, file_path: str, is_error: bool,
    ) -> list[HarnessFinding]:
        """Run post_tool sensors. Returns findings for agent context."""
        if is_error or tool_name not in _WRITE_TOOLS:
            return []

        findings: list[HarnessFinding] = []
        for ctrl in self._controls_by("sensor", "post_tool"):
            new_findings = await self._run_sensor(ctrl, tool_name, file_path)
            findings.extend(new_findings)
        return findings

    async def _run_sensor(
        self, ctrl: HarnessControl, tool_name: str, file_path: str
    ) -> list[HarnessFinding]:
        if ctrl.name == "lsp_diagnose":
            return await lsp_diagnose_sensor(lsp_manager=self.lsp_manager, file_path=file_path)
        if ctrl.name == "code_rules":
            return code_rules_sensor(cwd=self._cwd, file_path=file_path)
        if ctrl.name == "auto_commit":
            finding = auto_commit_sensor(file_path=Path(file_path), tool_name=tool_name)
            return [finding] if finding else []
        return []

    def enable(self, name: str) -> None:
        self._overrides[name] = True

    def disable(self, name: str) -> None:
        self._overrides[name] = False

    def status(self) -> dict:
        guides = []
        sensors = []
        for ctrl in self._config.controls:
            entry = {
                "name": ctrl.name,
                "trigger": ctrl.trigger,
                "kind": ctrl.kind,
                "enabled": self._is_enabled(ctrl),
            }
            if ctrl.category == "guide":
                guides.append(entry)
            else:
                sensors.append(entry)
        return {"template": self._config.template, "guides": guides, "sensors": sensors}

"""Tests for :class:`PermissionCheckComponent` — v12 M2 Task 2.6.

Wraps :class:`llm_code.runtime.permissions.PermissionPolicy` as a
``@component`` exposing the (allowed, reason) decision as output sockets
so downstream pipeline stages can branch on the result without reaching
back into the policy object.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.6
"""
from __future__ import annotations


from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.tools.base import PermissionLevel


def _policy(
    mode: PermissionMode = PermissionMode.FULL_ACCESS,
    *,
    allow: frozenset[str] = frozenset(),
    deny: frozenset[str] = frozenset(),
) -> PermissionPolicy:
    """Build a :class:`PermissionPolicy` with explicit allow/deny lists."""
    return PermissionPolicy(mode=mode, allow_tools=allow, deny_tools=deny)


class TestPermissionCheckComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import permission_check as pc_mod

        assert hasattr(pc_mod, "PermissionCheckComponent")


class TestPermissionCheckComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy())
        assert is_component(comp)

    def test_declares_inputs_from_run_signature(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        inputs = get_input_sockets(PermissionCheckComponent)
        assert set(inputs) == {"tool_name", "tool_args", "is_read_only"}

    def test_declares_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        outputs = get_output_sockets(PermissionCheckComponent)
        assert set(outputs) == {"allowed", "reason"}
        assert outputs["allowed"].type is bool
        assert outputs["reason"].type is str


class TestPermissionCheckComponentRun:
    def test_read_only_tool_allowed_in_read_only_mode(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.READ_ONLY))
        result = comp.run(
            tool_name="read_file",
            tool_args={"path": "a.txt"},
            is_read_only=True,
        )
        assert result["allowed"] is True
        assert result["reason"] == ""

    def test_write_tool_denied_in_read_only_mode(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.READ_ONLY))
        result = comp.run(
            tool_name="write_file",
            tool_args={"path": "a.txt"},
            is_read_only=False,
        )
        assert result["allowed"] is False
        assert "denied" in result["reason"].lower() or result["reason"]

    def test_deny_list_wins(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(
            _policy(PermissionMode.FULL_ACCESS, deny=frozenset({"bash"})),
        )
        result = comp.run(tool_name="bash", tool_args={}, is_read_only=False)
        assert result["allowed"] is False

    def test_allow_list_overrides_mode(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(
            _policy(PermissionMode.READ_ONLY, allow=frozenset({"write_file"})),
        )
        result = comp.run(
            tool_name="write_file", tool_args={}, is_read_only=False,
        )
        assert result["allowed"] is True

    def test_auto_accept_allows_everything(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.AUTO_ACCEPT))
        assert comp.run(
            tool_name="bash", tool_args={}, is_read_only=False,
        )["allowed"]
        assert comp.run(
            tool_name="read_file", tool_args={}, is_read_only=True,
        )["allowed"]

    def test_prompt_mode_read_only_allowed(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.PROMPT))
        result = comp.run(
            tool_name="read_file", tool_args={}, is_read_only=True,
        )
        assert result["allowed"] is True

    def test_prompt_mode_write_needs_prompt_reported_as_denied(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.PROMPT))
        # NEED_PROMPT is not ALLOWED for the component; caller surfaces UI.
        result = comp.run(
            tool_name="bash", tool_args={}, is_read_only=False,
        )
        assert result["allowed"] is False
        assert "prompt" in result["reason"].lower()

    def test_plan_mode_denies_write_tool(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.PLAN))
        result = comp.run(
            tool_name="write_file", tool_args={}, is_read_only=False,
        )
        assert result["allowed"] is False
        assert "plan" in result["reason"].lower()

    def test_plan_mode_allows_read_only(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.PLAN))
        result = comp.run(
            tool_name="read_file", tool_args={}, is_read_only=True,
        )
        assert result["allowed"] is True

    def test_result_is_plain_dict(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.FULL_ACCESS))
        result = comp.run(tool_name="read_file", tool_args={}, is_read_only=True)
        assert isinstance(result, dict)
        assert set(result) == {"allowed", "reason"}
        assert isinstance(result["allowed"], bool)
        assert isinstance(result["reason"], str)

    def test_is_read_only_flag_propagates(self) -> None:
        """``is_read_only=True`` bypasses mode-level checks only when the
        policy would otherwise downgrade the request."""
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        # READ_ONLY mode + tool that is itself read-only: allowed.
        comp = PermissionCheckComponent(_policy(PermissionMode.READ_ONLY))
        assert comp.run(
            tool_name="read_file", tool_args={}, is_read_only=True,
        )["allowed"]

    def test_reason_nonempty_on_denial(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.READ_ONLY))
        result = comp.run(
            tool_name="bash", tool_args={}, is_read_only=False,
        )
        assert result["allowed"] is False
        assert result["reason"] != ""

    def test_effective_level_used(self) -> None:
        """Custom required_level override is honoured."""
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(
            _policy(PermissionMode.READ_ONLY),
            default_required=PermissionLevel.WORKSPACE_WRITE,
        )
        # is_read_only is False and the required level is workspace_write
        # which exceeds READ_ONLY mode's allowed max -> denied.
        result = comp.run(
            tool_name="anything", tool_args={}, is_read_only=False,
        )
        assert result["allowed"] is False


class TestPermissionCheckComponentWiresInPipeline:
    def test_add_to_pipeline_accepts_instance(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        comp = PermissionCheckComponent(_policy())
        p.add_component("perm", comp)
        assert "perm" in p._components

    def test_entry_sockets_expose_inputs(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("perm", PermissionCheckComponent(_policy()))
        entry = p.inputs()
        assert set(entry["perm"]) == {"tool_name", "tool_args", "is_read_only"}

    def test_pipeline_run_executes_component(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component(
            "perm", PermissionCheckComponent(_policy(PermissionMode.FULL_ACCESS)),
        )
        outputs = p.run({
            "perm": {
                "tool_name": "read_file",
                "tool_args": {},
                "is_read_only": True,
            },
        })
        assert outputs["perm"]["allowed"] is True


class TestPermissionCheckComponentDenyReason:
    """Reason strings should be informative for observability."""

    def test_deny_list_reason_mentions_tool(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(
            _policy(PermissionMode.FULL_ACCESS, deny=frozenset({"bash"})),
        )
        result = comp.run(tool_name="bash", tool_args={}, is_read_only=False)
        assert "deny" in result["reason"].lower()

    def test_read_only_mode_denial_reason_mentions_mode(self) -> None:
        from llm_code.engine.components.permission_check import (
            PermissionCheckComponent,
        )

        comp = PermissionCheckComponent(_policy(PermissionMode.READ_ONLY))
        result = comp.run(
            tool_name="write_file", tool_args={}, is_read_only=False,
        )
        assert "read_only" in result["reason"].lower() or "read-only" in result["reason"].lower()

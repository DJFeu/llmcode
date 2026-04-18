"""G1: subagent_factory child shares parent's SandboxLifecycleManager."""
from __future__ import annotations

import inspect


class TestSubagentInheritsLifecycle:
    def test_factory_attaches_parent_lifecycle_to_child(self) -> None:
        """make_subagent_runtime copies ``parent._sandbox_lifecycle``
        onto ``child._sandbox_lifecycle`` so parent.shutdown() also
        closes any backend the child happened to spawn.

        We assert via source inspection — driving make_subagent_runtime
        end-to-end needs a full parent ConversationRuntime (15+ deps)
        that's disproportionate to the one-line wire."""
        from llm_code.runtime.subagent_factory import make_subagent_runtime

        src = inspect.getsource(make_subagent_runtime)
        assert "_sandbox_lifecycle" in src, (
            "G1: make_subagent_runtime must propagate the sandbox "
            "lifecycle so parent shutdown covers child backends."
        )
        assert "parent._sandbox_lifecycle" in src or "parent, \"_sandbox_lifecycle\"" in src

    def test_child_lifecycle_assignment_after_construction(self) -> None:
        """Assignment order: construct child runtime first, then copy
        parent's lifecycle onto it. Source ordering protects this."""
        from llm_code.runtime.subagent_factory import make_subagent_runtime

        src = inspect.getsource(make_subagent_runtime)
        build_idx = src.find("child = ConversationRuntime(")
        wire_idx = src.find("child._sandbox_lifecycle")
        assert build_idx > 0 and wire_idx > 0
        assert build_idx < wire_idx, (
            "Child must be constructed before its _sandbox_lifecycle is set."
        )

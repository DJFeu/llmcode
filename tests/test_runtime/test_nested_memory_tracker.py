"""M4: nested memory path tracker."""
from __future__ import annotations


class TestNestedPathTracker:
    def test_register_and_list(self) -> None:
        from llm_code.runtime.nested_memory_tracker import (
            NestedMemoryTracker,
        )
        t = NestedMemoryTracker()
        t.register_attachment("/proj/a/MEMORY.md")
        t.register_attachment("/proj/b/MEMORY.md")
        assert t.attached == {"/proj/a/MEMORY.md", "/proj/b/MEMORY.md"}

    def test_register_load_tracks_separately(self) -> None:
        from llm_code.runtime.nested_memory_tracker import (
            NestedMemoryTracker,
        )
        t = NestedMemoryTracker()
        t.register_attachment("/p/MEMORY.md")
        assert "/p/MEMORY.md" not in t.loaded
        t.register_load("/p/MEMORY.md")
        assert "/p/MEMORY.md" in t.loaded

    def test_double_register_is_no_op(self) -> None:
        from llm_code.runtime.nested_memory_tracker import (
            NestedMemoryTracker,
        )
        t = NestedMemoryTracker()
        t.register_attachment("/p/MEMORY.md")
        t.register_attachment("/p/MEMORY.md")
        assert len(t.attached) == 1

    def test_report_shape(self) -> None:
        from llm_code.runtime.nested_memory_tracker import (
            NestedMemoryTracker,
        )
        t = NestedMemoryTracker()
        t.register_attachment("/p/a")
        t.register_load("/p/a")
        t.register_attachment("/p/b")
        r = t.report()
        assert r["attached"] == 2
        assert r["loaded"] == 1
        assert r["pending"] == 1

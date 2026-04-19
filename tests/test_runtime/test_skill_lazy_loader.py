"""M12: skill lazy-load helper."""
from __future__ import annotations


class TestLazyLoader:
    def test_defers_until_first_call(self) -> None:
        from llm_code.runtime.skill_lazy_loader import lazy_skill

        calls = []

        @lazy_skill
        def heavy_factory():
            calls.append(1)
            return {"ready": True}

        # Construction didn't invoke the factory.
        assert calls == []
        # First access triggers load.
        skill = heavy_factory()
        assert skill == {"ready": True}
        assert calls == [1]
        # Second access is cached.
        skill2 = heavy_factory()
        assert skill2 is skill
        assert calls == [1]

    def test_reset_forces_reload(self) -> None:
        from llm_code.runtime.skill_lazy_loader import lazy_skill

        counter = [0]

        @lazy_skill
        def factory():
            counter[0] += 1
            return counter[0]

        assert factory() == 1
        assert factory() == 1
        factory.reset()
        assert factory() == 2

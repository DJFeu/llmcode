"""Backend health tracking + smart-fallback ordering tests (v2.8.0 M4)."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from llm_code.tools.search_backends import health as h
from llm_code.tools.search_backends import SearchResult


@pytest.fixture(autouse=True)
def _reset_health() -> None:
    """Per-process state resets between tests."""
    h._reset_for_tests()
    yield
    h._reset_for_tests()


class TestRecordFailure:
    def test_first_failure_does_not_open_circuit(self) -> None:
        h.record_failure("brave", kind="rate_limit")
        assert h.is_healthy("brave") is True

    def test_two_failures_does_not_open_circuit(self) -> None:
        h.record_failure("brave", kind="error")
        h.record_failure("brave", kind="error")
        assert h.is_healthy("brave") is True

    def test_three_consecutive_failures_opens_circuit(self) -> None:
        for _ in range(3):
            h.record_failure("brave", kind="rate_limit")
        assert h.is_healthy("brave") is False

    def test_record_failure_kinds(self) -> None:
        # Each kind is a valid argument; rate_limit also stamps last_429_at.
        h.record_failure("a", kind="rate_limit")
        snap = h.snapshot("a")
        assert snap is not None
        assert snap.last_429_at > 0

        h.record_failure("b", kind="timeout")
        h.record_failure("c", kind="error")
        # No exception means accepted.

    def test_circuit_open_emits_warn_log(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        with caplog.at_level(logging.WARNING, logger="llm_code.tools.search_backends.health"):
            for _ in range(3):
                h.record_failure("brave", kind="rate_limit")
        assert any("circuit_open" in rec.message for rec in caplog.records)


class TestRecordSuccess:
    def test_success_resets_failure_counter(self) -> None:
        h.record_failure("brave")
        h.record_failure("brave")
        h.record_success("brave")
        # Now two more failures should not yet open the circuit.
        h.record_failure("brave")
        h.record_failure("brave")
        assert h.is_healthy("brave") is True

    def test_success_closes_open_circuit(self) -> None:
        for _ in range(3):
            h.record_failure("brave")
        assert h.is_healthy("brave") is False
        h.record_success("brave")
        assert h.is_healthy("brave") is True

    def test_circuit_close_emits_info_log(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        for _ in range(3):
            h.record_failure("brave")
        with caplog.at_level(
            logging.INFO, logger="llm_code.tools.search_backends.health",
        ):
            h.record_success("brave")
        assert any("circuit_close" in rec.message for rec in caplog.records)

    def test_success_on_clean_backend_does_not_log_close(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A success when the circuit was already closed must not log
        # circuit_close (would be noise).
        import logging
        with caplog.at_level(
            logging.INFO, logger="llm_code.tools.search_backends.health",
        ):
            h.record_success("brave")
        assert not any("circuit_close" in rec.message for rec in caplog.records)


class TestIsHealthyAutoReset:
    def test_circuit_auto_resets_after_window_passes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = [1000.0]

        def fake_now() -> float:
            return clock[0]

        monkeypatch.setattr(h, "_now", fake_now)
        for _ in range(3):
            h.record_failure("brave")
        assert h.is_healthy("brave") is False
        # Advance past the 5-min window.
        clock[0] += 301.0
        assert h.is_healthy("brave") is True


class TestSortChain:
    def test_healthy_chain_unchanged(self) -> None:
        chain = ("a", "b", "c")
        assert h.sort_chain(chain) == chain

    def test_unhealthy_moved_to_end(self) -> None:
        for _ in range(3):
            h.record_failure("b")
        assert h.sort_chain(("a", "b", "c")) == ("a", "c", "b")

    def test_relative_order_preserved_in_partitions(self) -> None:
        # Open circuits on a and c. Healthy: b. Order: b, a, c.
        for _ in range(3):
            h.record_failure("a")
        for _ in range(3):
            h.record_failure("c")
        assert h.sort_chain(("a", "b", "c")) == ("b", "a", "c")

    def test_all_unhealthy_preserves_order(self) -> None:
        for _ in range(3):
            h.record_failure("a")
        for _ in range(3):
            h.record_failure("b")
        # Both unhealthy → sort_chain falls through to "preserve relative order".
        assert h.sort_chain(("a", "b")) == ("a", "b")


class TestConcurrentAccess:
    def test_concurrent_record_failure_no_race(self) -> None:
        # 3 threads firing 10 failures each → counter must reach 30.
        def worker() -> None:
            for _ in range(10):
                h.record_failure("brave")

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = h.snapshot("brave")
        assert snap is not None
        assert snap.consecutive_failures == 30
        assert h.is_healthy("brave") is False


class TestSnapshot:
    def test_snapshot_returns_none_for_unknown(self) -> None:
        assert h.snapshot("never-touched") is None

    def test_snapshot_returns_copy(self) -> None:
        h.record_failure("brave")
        snap = h.snapshot("brave")
        assert snap is not None
        assert snap.consecutive_failures == 1
        # Mutating the snapshot must not affect the live record.
        snap.consecutive_failures = 999
        snap2 = h.snapshot("brave")
        assert snap2 is not None
        assert snap2.consecutive_failures == 1


class TestWebSearchIntegration:
    def test_fallback_records_failure_on_rate_limit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.web_search import WebSearchTool
        from llm_code.tools.search_backends import RateLimitError

        call_log: list[str] = []

        def fake_create(name: str, **kwargs: object) -> object:
            class FakeBackend:
                @property
                def name(self) -> str:
                    return name

                def search(self, query: str, *, max_results: int = 10) -> tuple:
                    call_log.append(name)
                    if name == "duckduckgo":
                        raise RateLimitError("simulated 429")
                    return (
                        SearchResult(title="ok", url="https://ok.com", snippet="ok"),
                    )
            return FakeBackend()

        monkeypatch.setattr(
            "llm_code.tools.web_search.create_backend", fake_create,
        )
        # Disable extra backends — only DDG and brave in the chain.
        for var in ("EXA_API_KEY", "JINA_API_KEY", "LINKUP_API_KEY",
                    "TAVILY_API_KEY", "SERPER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("BRAVE_API_KEY", "test")

        from unittest.mock import MagicMock
        cfg = MagicMock(
            brave_api_key_env="BRAVE_API_KEY",
            exa_api_key_env="EXA_API_KEY",
            jina_api_key_env="JINA_API_KEY",
            linkup_api_key_env="LINKUP_API_KEY",
            searxng_base_url="",
            serper_api_key_env="SERPER_API_KEY",
            tavily_api_key_env="TAVILY_API_KEY",
        )

        tool = WebSearchTool()
        results = tool._search_with_fallback("query", 10, cfg)
        assert len(results) == 1
        snap = h.snapshot("duckduckgo")
        assert snap is not None
        assert snap.consecutive_failures >= 1

    def test_fallback_resets_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from llm_code.tools.web_search import WebSearchTool

        def fake_create(name: str, **kwargs: object) -> object:
            class FakeBackend:
                @property
                def name(self) -> str:
                    return name

                def search(self, query: str, *, max_results: int = 10) -> tuple:
                    return (
                        SearchResult(title="t", url="https://x.com", snippet="s"),
                    )
            return FakeBackend()

        monkeypatch.setattr(
            "llm_code.tools.web_search.create_backend", fake_create,
        )
        for var in ("BRAVE_API_KEY", "EXA_API_KEY", "JINA_API_KEY",
                    "LINKUP_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        # Pre-populate failures on duckduckgo.
        for _ in range(2):
            h.record_failure("duckduckgo")

        from unittest.mock import MagicMock
        cfg = MagicMock(
            brave_api_key_env="BRAVE_API_KEY",
            exa_api_key_env="EXA_API_KEY",
            jina_api_key_env="JINA_API_KEY",
            linkup_api_key_env="LINKUP_API_KEY",
            searxng_base_url="",
            serper_api_key_env="SERPER_API_KEY",
            tavily_api_key_env="TAVILY_API_KEY",
        )
        tool = WebSearchTool()
        results = tool._search_with_fallback("q", 10, cfg)
        assert len(results) == 1
        snap = h.snapshot("duckduckgo")
        assert snap is not None
        assert snap.consecutive_failures == 0

    def test_health_check_disabled_walks_chain_unsorted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Profile flag off → ``sort_chain`` skipped, walks original order."""
        from llm_code.tools.web_search import WebSearchTool

        # Open the circuit on duckduckgo.
        for _ in range(3):
            h.record_failure("duckduckgo")

        call_log: list[str] = []

        def fake_create(name: str, **kwargs: object) -> object:
            class FakeBackend:
                @property
                def name(self) -> str:
                    return name

                def search(self, query: str, *, max_results: int = 10) -> tuple:
                    call_log.append(name)
                    return ()
            return FakeBackend()

        monkeypatch.setattr(
            "llm_code.tools.web_search.create_backend", fake_create,
        )
        for var in ("BRAVE_API_KEY", "EXA_API_KEY", "JINA_API_KEY",
                    "LINKUP_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        from unittest.mock import MagicMock
        cfg = MagicMock(
            brave_api_key_env="BRAVE_API_KEY",
            exa_api_key_env="EXA_API_KEY",
            jina_api_key_env="JINA_API_KEY",
            linkup_api_key_env="LINKUP_API_KEY",
            searxng_base_url="",
            serper_api_key_env="SERPER_API_KEY",
            tavily_api_key_env="TAVILY_API_KEY",
        )
        tool = WebSearchTool()
        with patch.object(tool, "_health_check_enabled", return_value=False):
            tool._search_with_fallback("q", 10, cfg)
        # When disabled, duckduckgo is still tried first (chain order).
        assert call_log[0] == "duckduckgo"

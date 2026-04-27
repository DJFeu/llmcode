"""Local rerank backend tests (v2.8.0 M1).

These tests inject a fake ``CrossEncoder`` via ``sys.modules`` so the
local backend's plumbing (lazy load + score-descending sort + index
preservation) is exercised without paying the ~80MB model download
cost in CI. Manual smoke against the real model is documented in the
spec §8 acceptance criteria.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from llm_code.tools.rerank import RerankResult


@pytest.fixture(autouse=True)
def _reset_model_cache() -> Any:
    """Each test starts with a clean model cache.

    Tests inject a different fake module per case so a stale cached
    model from a prior test must not bleed across.
    """
    from llm_code.tools.rerank import local as local_mod
    local_mod._model_cache.clear()
    yield
    local_mod._model_cache.clear()


def _install_fake_st(monkeypatch: pytest.MonkeyPatch, mock: Any) -> None:
    """Install a fake ``sentence_transformers`` module with the given mock."""
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.CrossEncoder = mock  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)


class TestLocalConstruction:
    def test_backend_name(self) -> None:
        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        assert backend.name == "local"

    def test_construction_does_not_load_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Lazy load — constructor must not import sentence_transformers.
        # We don't install a fake module; if construction tried to
        # import the package the test would fail with ImportError.
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # type: ignore[arg-type]
        from llm_code.tools.rerank.local import LocalRerankBackend
        # Construction succeeds even with a poisoned module.
        backend = LocalRerankBackend()
        assert backend.name == "local"


class TestLocalRerank:
    def test_empty_documents_returns_empty_no_load(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Empty docs short-circuits before model load.
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # type: ignore[arg-type]
        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        assert backend.rerank("q", (), top_k=5) == ()

    def test_missing_extra_raises_clear_import_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Simulate the [memory] extra not being installed.
        import builtins
        real_import = builtins.__import__

        def raising_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "sentence_transformers":
                raise ImportError("no module named sentence_transformers")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", raising_import)
        # Ensure the cache is empty so the lazy load path runs.
        from llm_code.tools.rerank import local as local_mod
        local_mod._model_cache.clear()

        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        with pytest.raises(ImportError, match=r"\[memory\]"):
            backend.rerank("q", ("a", "b"), top_k=2)

    def test_rerank_orders_by_score_descending(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Fake CrossEncoder.predict returns a fixed score vector.
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.10, 0.95, 0.50]
        ce_factory = MagicMock(return_value=mock_model)
        _install_fake_st(monkeypatch, ce_factory)

        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        docs = ("first", "second", "third")
        results = backend.rerank("query", docs, top_k=3)
        assert len(results) == 3
        # Highest score first.
        assert results[0].document == "second"
        assert results[0].original_index == 1
        assert results[0].score == pytest.approx(0.95)
        assert results[1].document == "third"
        assert results[1].original_index == 2
        assert results[2].document == "first"
        assert results[2].original_index == 0
        for r in results:
            assert isinstance(r, RerankResult)

    def test_top_k_caps_results(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.9, 0.1, 0.7, 0.3]
        _install_fake_st(monkeypatch, MagicMock(return_value=mock_model))

        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        docs = tuple(f"d{i}" for i in range(5))
        results = backend.rerank("q", docs, top_k=2)
        assert len(results) == 2
        # Best two: idx 1 (0.9), idx 3 (0.7)
        assert results[0].original_index == 1
        assert results[1].original_index == 3

    def test_model_loaded_once_across_calls(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5]
        ce_factory = MagicMock(return_value=mock_model)
        _install_fake_st(monkeypatch, ce_factory)

        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        backend.rerank("q1", ("a",), top_k=1)
        backend.rerank("q2", ("b",), top_k=1)
        # CrossEncoder factory called only once — model is cached at
        # the module level after the first load.
        assert ce_factory.call_count == 1

    def test_query_passed_to_predict(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.5]
        _install_fake_st(monkeypatch, MagicMock(return_value=mock_model))

        from llm_code.tools.rerank.local import LocalRerankBackend
        backend = LocalRerankBackend()
        backend.rerank("the query", ("a", "b"), top_k=2)
        # Model.predict received [(query, doc), ...] pairs.
        args, _ = mock_model.predict.call_args
        pairs = list(args[0])
        assert pairs == [("the query", "a"), ("the query", "b")]

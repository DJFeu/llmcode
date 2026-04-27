"""RerankTool integration tests (v2.8.0 M1)."""
from __future__ import annotations

from unittest.mock import patch

from llm_code.tools.rerank import IdentityRerankBackend
from llm_code.tools.rerank_tool import RerankInput, RerankTool


class TestRerankToolMetadata:
    def test_name(self) -> None:
        tool = RerankTool()
        assert tool.name == "rerank"

    def test_description_mentions_rerank(self) -> None:
        tool = RerankTool()
        assert "Rerank" in tool.description or "rerank" in tool.description

    def test_input_schema_required_fields(self) -> None:
        tool = RerankTool()
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "documents" in schema["properties"]
        assert set(schema["required"]) == {"query", "documents"}

    def test_input_model(self) -> None:
        tool = RerankTool()
        assert tool.input_model is RerankInput

    def test_read_only_permission(self) -> None:
        from llm_code.tools.base import PermissionLevel
        tool = RerankTool()
        assert tool.required_permission == PermissionLevel.READ_ONLY

    def test_concurrency_safe(self) -> None:
        tool = RerankTool()
        assert tool.is_concurrency_safe({}) is True

    def test_is_read_only(self) -> None:
        tool = RerankTool()
        assert tool.is_read_only({}) is True


class TestRerankToolExecute:
    def test_invalid_input_returns_error(self) -> None:
        tool = RerankTool()
        result = tool.execute({"documents": ["a"]})  # missing query
        assert result.is_error

    def test_empty_documents_returns_zero_count(self) -> None:
        tool = RerankTool()
        result = tool.execute({"query": "q", "documents": []})
        assert not result.is_error
        assert "0 documents" in result.output

    def test_uses_identity_backend_when_profile_none(self) -> None:
        tool = RerankTool()
        with patch.object(tool, "_resolve_backend_name", return_value="none"):
            result = tool.execute({
                "query": "q",
                "documents": ["alpha", "beta", "gamma"],
                "top_k": 2,
            })
        assert not result.is_error
        # Identity backend preserves input order.
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "(2 results)" in result.output

    def test_unknown_backend_name_surfaces_error(self) -> None:
        tool = RerankTool()
        with patch.object(tool, "_resolve_backend_name", return_value="bogus"):
            result = tool.execute({
                "query": "q",
                "documents": ["a"],
                "top_k": 1,
            })
        assert result.is_error
        assert "bogus" in result.output

    def test_output_includes_backend_name_and_scores(self) -> None:
        tool = RerankTool()
        with patch.object(tool, "_resolve_backend_name", return_value="none"):
            result = tool.execute({
                "query": "machine learning",
                "documents": ["doc one", "doc two"],
                "top_k": 2,
            })
        assert "(backend: none)" in result.output
        assert "score=" in result.output

    def test_output_truncates_long_documents(self) -> None:
        tool = RerankTool()
        long_doc = "x" * 500
        with patch.object(tool, "_resolve_backend_name", return_value="none"):
            result = tool.execute({
                "query": "q",
                "documents": [long_doc],
                "top_k": 1,
            })
        # 200-char preview cap + ellipsis marker.
        assert "…" in result.output


class TestRerankToolBackendResolution:
    def test_resolve_backend_falls_back_to_local_on_exception(self) -> None:
        """When runtime config / profile lookups fail the tool returns ``"local"``."""
        tool = RerankTool()
        # Patching the import so it raises forces the except branch.
        with patch(
            "llm_code.runtime.config.RuntimeConfig",
            side_effect=Exception("oops"),
        ):
            assert tool._resolve_backend_name() == "local"


class TestIdentityViaTool:
    def test_identity_backend_full_path(self) -> None:
        backend = IdentityRerankBackend()
        results = backend.rerank("q", ("first", "second", "third"), top_k=10)
        assert len(results) == 3
        assert [r.document for r in results] == ["first", "second", "third"]

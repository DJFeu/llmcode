"""Tests for KnowledgeCompiler."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_knowledge_entry_fields():
    from llm_code.runtime.knowledge_compiler import KnowledgeEntry

    entry = KnowledgeEntry(
        path="modules/api.md",
        title="API Layer",
        summary="OpenAI-compatible REST API",
        last_compiled="2026-04-06T12:00:00Z",
        source_files=("llm_code/api/openai_compat.py",),
    )
    assert entry.path == "modules/api.md"
    assert entry.title == "API Layer"
    assert entry.source_files == ("llm_code/api/openai_compat.py",)


def test_knowledge_entry_frozen():
    from llm_code.runtime.knowledge_compiler import KnowledgeEntry

    entry = KnowledgeEntry(path="x", title="x", summary="x", last_compiled="x", source_files=())
    try:
        entry.path = "y"  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_compiler_init_creates_dirs(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    knowledge_dir = tmp_path / ".llmcode" / "knowledge"
    assert knowledge_dir.exists()
    assert (knowledge_dir / "modules").exists()


def test_compiler_get_index_empty(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    index = compiler.get_index()
    assert index == []


def test_compiler_get_index_reads_files(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    knowledge_dir = tmp_path / ".llmcode" / "knowledge"

    index_md = knowledge_dir / "index.md"
    index_md.write_text(
        "# Knowledge Index\n\n"
        "- [API Layer](modules/api.md) — OpenAI-compatible REST API\n"
        "- [Runtime](modules/runtime.md) — Conversation engine\n"
    )
    (knowledge_dir / "modules" / "api.md").write_text("# API Layer\nHandles requests.\n")
    (knowledge_dir / "modules" / "runtime.md").write_text("# Runtime\nConversation engine.\n")

    index = compiler.get_index()
    assert len(index) == 2
    assert index[0].title == "API Layer"
    assert index[0].path == "modules/api.md"
    assert index[0].summary == "OpenAI-compatible REST API"


# --- Task 3: Ingest ---

def test_ingest_collects_git_diff(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)

    fake_diff = "llm_code/api/openai_compat.py\nllm_code/runtime/session.py\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_diff, returncode=0)
        result = compiler.ingest(facts=[], since_commit="abc123")

    assert "llm_code/api/openai_compat.py" in result.changed_files
    assert "llm_code/runtime/session.py" in result.changed_files


def test_ingest_includes_facts(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = compiler.ingest(
            facts=["Added auth middleware", "Refactored API layer"],
            since_commit=None,
        )

    assert len(result.facts) == 2
    assert "Added auth middleware" in result.facts


def test_ingest_handles_git_failure(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)

    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = compiler.ingest(facts=["some fact"], since_commit="abc")

    assert result.changed_files == ()
    assert result.facts == ("some fact",)


def test_ingest_result_frozen(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import IngestResult

    result = IngestResult(changed_files=("a.py",), facts=("fact1",))
    try:
        result.changed_files = ("b.py",)  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass


# --- Task 4: Compile ---

@pytest.mark.asyncio
async def test_compile_generates_index(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler, IngestResult

    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = (MagicMock(text="# API Layer\n\nHandles OpenAI-compatible requests.\n\n## Key Types\n- MessageRequest"),)
    mock_provider.send_message.return_value = mock_response

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=mock_provider)
    ingest_data = IngestResult(
        changed_files=("llm_code/api/openai_compat.py",),
        facts=("Added streaming support",),
    )

    await compiler.compile(ingest_data)

    index_path = tmp_path / ".llmcode" / "knowledge" / "index.md"
    assert index_path.exists()
    content = index_path.read_text()
    assert "api" in content.lower()


@pytest.mark.asyncio
async def test_compile_creates_module_file(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler, IngestResult

    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = (MagicMock(text="# API Layer\n\nHandles requests."),)
    mock_provider.send_message.return_value = mock_response

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=mock_provider)
    ingest_data = IngestResult(
        changed_files=("llm_code/api/openai_compat.py",),
        facts=(),
    )

    await compiler.compile(ingest_data)

    modules_dir = tmp_path / ".llmcode" / "knowledge" / "modules"
    md_files = list(modules_dir.glob("*.md"))
    assert len(md_files) >= 1


@pytest.mark.asyncio
async def test_compile_skips_when_no_provider(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler, IngestResult

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    ingest_data = IngestResult(changed_files=("a.py",), facts=("fact",))

    await compiler.compile(ingest_data)

    index_path = tmp_path / ".llmcode" / "knowledge" / "index.md"
    assert not index_path.exists()


@pytest.mark.asyncio
async def test_compile_skips_empty_ingest(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler, IngestResult

    mock_provider = AsyncMock()
    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=mock_provider)
    ingest_data = IngestResult(changed_files=(), facts=())

    await compiler.compile(ingest_data)

    mock_provider.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_compile_handles_llm_failure(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler, IngestResult

    mock_provider = AsyncMock()
    mock_provider.send_message.side_effect = RuntimeError("LLM down")

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=mock_provider)
    ingest_data = IngestResult(changed_files=("llm_code/api/foo.py",), facts=("fact",))

    # Should not raise
    await compiler.compile(ingest_data)


# --- Task 5: Query ---

def test_query_returns_relevant_knowledge(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    knowledge_dir = tmp_path / ".llmcode" / "knowledge"

    (knowledge_dir / "index.md").write_text(
        "# Knowledge Index\n\n"
        "- [Api](modules/api.md) — REST API layer\n"
        "- [Runtime](modules/runtime.md) — Conversation engine\n"
    )
    (knowledge_dir / "modules" / "api.md").write_text(
        "# API Layer\n\nHandles OpenAI-compatible requests.\n\n## Key Types\n- MessageRequest\n"
    )
    (knowledge_dir / "modules" / "runtime.md").write_text(
        "# Runtime\n\nConversation engine with turn loop.\n\n## Key Types\n- ConversationEngine\n"
    )

    result = compiler.query(max_tokens=3000)
    assert isinstance(result, str)
    assert "API Layer" in result or "api" in result.lower()
    assert len(result) > 0


def test_query_respects_token_budget(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    knowledge_dir = tmp_path / ".llmcode" / "knowledge"

    (knowledge_dir / "index.md").write_text("# Knowledge Index\n\n- [Big](modules/big.md) — Big module\n")
    (knowledge_dir / "modules" / "big.md").write_text("# Big\n\n" + "x " * 5000)

    result = compiler.query(max_tokens=100)
    assert len(result) <= 500


def test_query_empty_knowledge(tmp_path: Path):
    from llm_code.runtime.knowledge_compiler import KnowledgeCompiler

    compiler = KnowledgeCompiler(cwd=tmp_path, llm_provider=None)
    result = compiler.query(max_tokens=3000)
    assert result == ""

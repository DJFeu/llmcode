"""Tests for memory lint."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _setup_memory(tmp_path: Path, entries: dict) -> Path:
    """Helper: create a memory.json file with given entries."""
    memory_dir = tmp_path / "memory" / "abcd1234"
    memory_dir.mkdir(parents=True)
    memory_file = memory_dir / "memory.json"
    memory_file.write_text(json.dumps(entries, indent=2))
    return memory_dir


def test_stale_reference_detected(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    now = datetime.now(timezone.utc).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "api_notes": {
            "value": "The file llm_code/api/anthropic.py handles auth",
            "created_at": now, "updated_at": now,
        }
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert len(result.stale) >= 1
    assert any("anthropic.py" in s.reference for s in result.stale)


def test_no_stale_when_file_exists(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    now = datetime.now(timezone.utc).isoformat()
    cwd = tmp_path / "project"
    (cwd / "llm_code" / "api").mkdir(parents=True)
    (cwd / "llm_code" / "api" / "provider.py").write_text("class LLMProvider: pass\n")

    memory_dir = _setup_memory(tmp_path, {
        "api_notes": {
            "value": "The file llm_code/api/provider.py has the base class",
            "created_at": now, "updated_at": now,
        }
    })

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert len(result.stale) == 0


def test_coverage_gaps_detected(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    now = datetime.now(timezone.utc).isoformat()
    cwd = tmp_path / "project"
    (cwd / "llm_code" / "api").mkdir(parents=True)
    (cwd / "llm_code" / "api" / "__init__.py").write_text("")
    (cwd / "llm_code" / "harness").mkdir(parents=True)
    (cwd / "llm_code" / "harness" / "__init__.py").write_text("")

    memory_dir = _setup_memory(tmp_path, {
        "api_notes": {
            "value": "llm_code/api handles the API layer",
            "created_at": now, "updated_at": now,
        }
    })

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert any("harness" in gap for gap in result.coverage_gaps)


def test_old_entries_detected(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "old_note": {"value": "Some old info", "created_at": old_date, "updated_at": old_date}
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert "old_note" in result.old


def test_old_entries_not_flagged_if_recent(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    now = datetime.now(timezone.utc).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "fresh_note": {"value": "Recent info", "created_at": now, "updated_at": now}
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert "fresh_note" not in result.old


def test_internal_keys_skipped(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "_dream_last_run": {"value": "2026-04-01", "created_at": old_date, "updated_at": old_date}
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert "_dream_last_run" not in result.old
    assert len(result.stale) == 0


def test_lint_result_summary(tmp_path: Path):
    from llm_code.runtime.memory_validator import MemoryLintResult, StaleReference

    result = MemoryLintResult(
        stale=(StaleReference(key="k", reference="f.py", line=1),),
        contradictions=(),
        coverage_gaps=("llm_code/harness/",),
        orphans=(),
        old=("old_note",),
    )
    summary = result.format_summary()
    assert "1 stale" in summary
    assert "1 old" in summary


def test_lint_empty_memory(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory

    memory_dir = _setup_memory(tmp_path, {})
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = lint_memory(memory_dir=memory_dir, cwd=cwd)
    assert len(result.stale) == 0
    assert len(result.old) == 0


# --- Task 2: Deep lint ---

@pytest.mark.asyncio
async def test_deep_lint_detects_contradictions(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory_deep

    now = datetime.now(timezone.utc).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "arch_v1": {"value": "The system uses REST API exclusively", "created_at": now, "updated_at": now},
        "arch_v2": {"value": "The system uses GraphQL for all endpoints", "created_at": now, "updated_at": now},
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = (MagicMock(text='[{"key_a": "arch_v1", "key_b": "arch_v2", "description": "REST vs GraphQL conflict"}]'),)
    mock_provider.send_message.return_value = mock_response

    result = await lint_memory_deep(memory_dir=memory_dir, cwd=cwd, llm_provider=mock_provider)
    assert len(result.contradictions) >= 1


@pytest.mark.asyncio
async def test_deep_lint_skips_without_provider(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory_deep

    now = datetime.now(timezone.utc).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "note": {"value": "some text", "created_at": now, "updated_at": now},
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    result = await lint_memory_deep(memory_dir=memory_dir, cwd=cwd, llm_provider=None)
    assert len(result.contradictions) == 0


@pytest.mark.asyncio
async def test_deep_lint_handles_llm_failure(tmp_path: Path):
    from llm_code.runtime.memory_validator import lint_memory_deep

    now = datetime.now(timezone.utc).isoformat()
    memory_dir = _setup_memory(tmp_path, {
        "note": {"value": "text", "created_at": now, "updated_at": now},
    })
    cwd = tmp_path / "project"
    cwd.mkdir()

    mock_provider = AsyncMock()
    mock_provider.send_message.side_effect = RuntimeError("LLM down")

    result = await lint_memory_deep(memory_dir=memory_dir, cwd=cwd, llm_provider=mock_provider)
    assert len(result.contradictions) == 0

"""Tests for skill extraction from session transcripts."""
from __future__ import annotations

from pathlib import Path

from llm_code.runtime.skill_extractor import (
    SkillCandidate,
    extract_skill_candidates,
    render_skill_md,
    save_skill,
)


def _make_transcript(tool_names: list[str]) -> list[dict]:
    """Build a fake transcript with tool_use blocks."""
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": name, "id": f"id_{i}", "input": {}}
                for i, name in enumerate(tool_names)
            ],
        }
    ]


class TestExtractSkillCandidates:
    def test_repeated_pattern_found(self) -> None:
        pattern = ["read_file", "edit_file", "bash", "git_commit"]
        transcripts = [_make_transcript(pattern) for _ in range(3)]
        candidates = extract_skill_candidates(transcripts, min_occurrences=2, min_steps=3)
        assert len(candidates) > 0
        # Should find the 3+ step subsequence
        assert any(len(c.steps) >= 3 for c in candidates)

    def test_no_repetition_no_candidates(self) -> None:
        t1 = _make_transcript(["read_file", "edit_file", "bash"])
        t2 = _make_transcript(["web_search", "web_fetch", "write_file"])
        candidates = extract_skill_candidates([t1, t2], min_occurrences=2, min_steps=3)
        assert len(candidates) == 0

    def test_min_steps_filter(self) -> None:
        pattern = ["read_file", "bash"]  # only 2 steps
        transcripts = [_make_transcript(pattern) for _ in range(5)]
        candidates = extract_skill_candidates(transcripts, min_occurrences=2, min_steps=3)
        assert len(candidates) == 0

    def test_confidence_levels(self) -> None:
        pattern = ["read_file", "edit_file", "bash", "git_commit"]
        transcripts = [_make_transcript(pattern) for _ in range(5)]
        candidates = extract_skill_candidates(transcripts, min_occurrences=2, min_steps=3)
        # 5 occurrences → high confidence
        assert any(c.confidence == "high" for c in candidates)

    def test_sorted_by_confidence(self) -> None:
        p1 = ["read_file", "edit_file", "bash"]
        p2 = ["web_search", "web_fetch", "write_file"]
        # p1 appears 5 times (high), p2 appears 2 times (medium)
        transcripts = [_make_transcript(p1)] * 5 + [_make_transcript(p2)] * 2
        candidates = extract_skill_candidates(transcripts, min_occurrences=2, min_steps=3)
        if len(candidates) >= 2:
            assert candidates[0].confidence in ("high", "medium")

    def test_empty_transcripts(self) -> None:
        assert extract_skill_candidates([]) == []

    def test_no_tool_use_messages(self) -> None:
        transcripts = [[{"role": "user", "content": "hello"}]]
        assert extract_skill_candidates(transcripts) == []


class TestRenderSkillMd:
    def test_contains_frontmatter(self) -> None:
        candidate = SkillCandidate(
            name="auto-edit-workflow",
            description="Edit and commit",
            steps=("read_file", "edit_file", "git_commit"),
            confidence="high",
            source_turns=5,
        )
        md = render_skill_md(candidate)
        assert "---" in md
        assert "name: auto-edit-workflow" in md
        assert "confidence: high" in md
        assert "read_file" in md

    def test_steps_numbered(self) -> None:
        candidate = SkillCandidate(
            name="test", description="test", steps=("a", "b", "c"),
            confidence="low", source_turns=2,
        )
        md = render_skill_md(candidate)
        assert "1. Use `a` tool" in md
        assert "2. Use `b` tool" in md
        assert "3. Use `c` tool" in md


class TestSaveSkill:
    def test_creates_file(self, tmp_path: Path) -> None:
        candidate = SkillCandidate(
            name="auto-test-workflow",
            description="Test workflow",
            steps=("bash", "read_file"),
            confidence="medium",
            source_turns=3,
        )
        path = save_skill(candidate, tmp_path)
        assert path.exists()
        assert path.name == "SKILL.md"
        content = path.read_text()
        assert "auto-test-workflow" in content

    def test_creates_directory(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        candidate = SkillCandidate(
            name="my-skill", description="d", steps=("a",),
            confidence="low", source_turns=1,
        )
        path = save_skill(candidate, skills_dir)
        assert path.parent.name == "my-skill"
        assert path.parent.parent == skills_dir

    def test_safe_name_sanitization(self, tmp_path: Path) -> None:
        candidate = SkillCandidate(
            name="weird/name with spaces!",
            description="d", steps=("a",),
            confidence="low", source_turns=1,
        )
        path = save_skill(candidate, tmp_path)
        assert "/" not in path.parent.name
        assert " " not in path.parent.name

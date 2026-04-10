"""Skill extraction from session transcripts.

Borrowed from Gemini CLI's ``skill-extraction-agent.ts``.

Analyzes past session transcripts to identify repeatable procedures
and generates candidate SKILL.md files.  Candidates require explicit
user confirmation before being saved.

Design:
    - ``extract_skill_candidates()`` analyzes transcript text
    - Returns ``SkillCandidate`` objects (not files on disk)
    - Caller (TUI / CLI) presents candidates for user approval
    - ``save_skill()`` writes approved candidates to disk

Extraction heuristics (no LLM call — pattern matching):
    - Repeated tool sequences (same 3+ tool chain across turns)
    - Named procedures ("whenever I X, do Y")
    - Multi-step workflows with consistent ordering

Risk mitigations:
    - Candidates are NEVER auto-saved — user must confirm
    - No secrets in output (tool args are stripped)
    - Confidence levels (high/medium/low) help user prioritize
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillCandidate:
    """A candidate skill extracted from session history."""
    name: str
    description: str
    steps: tuple[str, ...]
    confidence: str  # "high" | "medium" | "low"
    source_turns: int  # how many turns this pattern appeared in


def extract_skill_candidates(
    transcripts: list[list[dict[str, Any]]],
    *,
    min_occurrences: int = 2,
    min_steps: int = 3,
) -> list[SkillCandidate]:
    """Analyze transcripts for repeatable tool-use patterns.

    Parameters
    ----------
    transcripts:
        List of session transcripts.  Each transcript is a list of
        message dicts with ``role`` and ``content`` keys.
    min_occurrences:
        Minimum times a pattern must appear to be considered.
    min_steps:
        Minimum tool calls in a sequence to qualify.

    Returns
    -------
    list[SkillCandidate]
        Candidate skills sorted by confidence (high first).
    """
    # Extract tool-call sequences from each transcript
    all_sequences: list[tuple[str, ...]] = []
    for transcript in transcripts:
        seq = _extract_tool_sequence(transcript)
        if len(seq) >= min_steps:
            all_sequences.append(tuple(seq))

    # Find repeated subsequences across transcripts
    subseq_counts: Counter[tuple[str, ...]] = Counter()
    for seq in all_sequences:
        # Extract all contiguous subsequences of length >= min_steps
        for length in range(min_steps, min(len(seq) + 1, 10)):
            for start in range(len(seq) - length + 1):
                subseq = seq[start:start + length]
                subseq_counts[subseq] += 1

    # Filter by minimum occurrences and build candidates
    candidates: list[SkillCandidate] = []
    seen_names: set[str] = set()

    for subseq, count in subseq_counts.most_common(20):
        if count < min_occurrences:
            continue

        name = _derive_skill_name(subseq)
        if name in seen_names:
            continue
        seen_names.add(name)

        confidence = "high" if count >= 4 else "medium" if count >= 2 else "low"
        candidates.append(SkillCandidate(
            name=name,
            description=f"Automated workflow: {' → '.join(subseq)}",
            steps=subseq,
            confidence=confidence,
            source_turns=count,
        ))

    # Sort: high confidence first, then by occurrence count
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (confidence_order[c.confidence], -c.source_turns))

    return candidates


def render_skill_md(candidate: SkillCandidate) -> str:
    """Render a SkillCandidate as SKILL.md content.

    The output is a valid SKILL.md with frontmatter.
    """
    steps_md = "\n".join(f"{i+1}. Use `{step}` tool" for i, step in enumerate(candidate.steps))
    return f"""---
name: {candidate.name}
description: {candidate.description}
confidence: {candidate.confidence}
---

# {candidate.name}

{candidate.description}

## Steps

{steps_md}

## Notes

- Extracted from {candidate.source_turns} session occurrences
- Confidence: {candidate.confidence}
- Review and customize before using in production
"""


def save_skill(
    candidate: SkillCandidate,
    skills_dir: Path,
) -> Path:
    """Save an approved skill candidate to disk.

    Returns the path to the created SKILL.md file.
    """
    skills_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w-]", "-", candidate.name).strip("-")
    skill_dir = skills_dir / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(render_skill_md(candidate), encoding="utf-8")
    return skill_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_tool_sequence(transcript: list[dict[str, Any]]) -> list[str]:
    """Extract the ordered sequence of tool names from a transcript."""
    tools: list[str] = []
    for msg in transcript:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name:
                        tools.append(name)
    return tools


def _derive_skill_name(tool_sequence: tuple[str, ...]) -> str:
    """Derive a human-readable skill name from a tool sequence."""
    # Use the most distinctive tool as the base name
    # Skip common tools that don't add meaning
    common = {"read_file", "bash", "glob_search", "grep_search"}
    distinctive = [t for t in tool_sequence if t not in common]
    if distinctive:
        base = distinctive[0].replace("_", "-")
    else:
        base = tool_sequence[0].replace("_", "-")

    return f"auto-{base}-workflow"

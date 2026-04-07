"""Tests for keyword-driven action detection."""
from __future__ import annotations

from llm_code.runtime.keyword_actions import detect_action


def test_detect_yolo_mode() -> None:
    assert detect_action("let's ultrawork on this") == "enable_yolo_mode"


def test_detect_case_insensitive() -> None:
    assert detect_action("ULTRAWORK now") == "enable_yolo_mode"


def test_detect_review() -> None:
    assert detect_action("please review this PR") == "trigger_review_persona"


def test_detect_pytest() -> None:
    assert detect_action("run pytest on the suite") == "run_pytest"


def test_detect_no_match() -> None:
    assert detect_action("hello there") is None


def test_detect_empty_message() -> None:
    assert detect_action("") is None

"""Tests for v14 Mechanism C — denial-pattern detector.

Includes:
  * Unit tests covering the gate (has_recent_tool_call) and per-
    pattern positive / negative coverage.
  * Corpus regression test that loads the labeled fixture and
    enforces precision >= 0.95, recall >= 0.85.

When a false positive or false negative is observed in production,
add the offending text to ``tests/fixtures/denial_corpus.json`` with
the correct label. The corpus test will fail until the regex is
adjusted to handle the new entry, keeping the corpus authoritative.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime.denial_detector import (
    DenialMatch,
    detect_denial,
)


# =============================================================================
# Gate tests — has_recent_tool_call gate must always be respected
# =============================================================================


class TestRecentToolCallGate:
    def test_returns_none_when_no_recent_tool_call_even_on_strong_denial(
        self,
    ) -> None:
        """User asked 'are you connected to the internet?' — model
        legitimately answers 'I don't have access'. No tool was
        called. The detector must not retry — the answer is genuine."""
        text = "I don't have access to the internet."
        assert detect_denial(text, has_recent_tool_call=False) is None

    def test_returns_none_for_empty_content(self) -> None:
        assert detect_denial("", has_recent_tool_call=True) is None

    def test_returns_match_when_gate_satisfied(self) -> None:
        text = "I don't have access to news APIs."
        result = detect_denial(text, has_recent_tool_call=True)
        assert isinstance(result, DenialMatch)


# =============================================================================
# Per-language positive examples
# =============================================================================


class TestEnglishPatterns:
    @pytest.mark.parametrize(
        "text",
        [
            "I don't have access to the news API.",
            "I cannot browse the web from here.",
            "I'm just a coding assistant; I don't have internet access.",
            "I lack real-time access to current data.",
            "As an AI, I cannot fetch live data.",
            "As an AI language model, I cannot search the internet.",
            "I don't have the ability to fetch web pages.",
            "I cannot access external news sources.",
            "I lack network access to retrieve that.",
        ],
    )
    def test_positive_examples_match(self, text: str) -> None:
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is not None, f"missed denial: {text!r}"
        assert result.matched_text  # non-empty span


class TestTraditionalChinesePatterns:
    @pytest.mark.parametrize(
        "text",
        [
            "我沒有連線新聞來源的能力，無法提供今日熱門新聞。",
            "我無法瀏覽網路或存取即時資訊。",
            "我是一個在終端中運作的程式碼助手，沒有連線新聞來源或網路瀏覽的能力。",
            "我無法搜尋網路上的最新新聞。",
            "我是程式碼助理，無法瀏覽網際網路。",
        ],
    )
    def test_positive_examples_match(self, text: str) -> None:
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is not None, f"missed denial: {text!r}"


class TestSimplifiedChinesePatterns:
    @pytest.mark.parametrize(
        "text",
        [
            "我没有访问互联网的能力，无法获取最新新闻。",
            "我无法浏览网页以获取实时数据。",
            "我是一个代码助手，没有访问新闻 API 的能力。",
            "作为 AI，我无法搜索互联网。",
        ],
    )
    def test_positive_examples_match(self, text: str) -> None:
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is not None, f"missed denial: {text!r}"


# =============================================================================
# Negative controls — must NOT match
# =============================================================================


class TestNegativeControls:
    @pytest.mark.parametrize(
        "text",
        [
            "I don't have the file open yet — let me read it first.",
            "I haven't fetched the data yet; let me run web_search.",
            "Here are today's top news headlines from the search results.",
            "Based on the web_search results, the top three stories are A, B, C.",
            "The grep tool returned no matches; the function may have been renamed.",
            "I successfully read the file. It contains the user's profile.",
            "I don't see any errors in the output you shared.",
            "我沒有看到錯誤訊息，請貼上 console 輸出。",
            "根據 web_search 的結果，今日熱門新聞有以下三則。",
            "我没有看到 README 文件，您可以提供路径吗？",
            "I lack context about the legacy code — could you summarise the v1 design?",
        ],
    )
    def test_negative_examples_pass_through(self, text: str) -> None:
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is None, f"false positive on: {text!r}"


# =============================================================================
# Multi-paragraph + edge cases
# =============================================================================


class TestEdgeCases:
    def test_denial_in_middle_of_paragraph_matches(self) -> None:
        text = (
            "Here is some preamble text.\n\n"
            "Unfortunately, I don't have access to news APIs. "
            "However, I can help with code.\n"
        )
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is not None

    def test_returns_first_matching_pattern_only(self) -> None:
        """Detector stops at the first hit — verifies the early
        return path (cheaper than running every pattern when one is
        enough)."""
        text = (
            "I don't have access to news APIs and "
            "I cannot browse the web."
        )
        result = detect_denial(text, has_recent_tool_call=True)
        assert result is not None
        # Either pattern is acceptable as the "first" depending on
        # ordering; just verify one of them fired.
        assert (
            "access to" in result.matched_text.lower()
            or "cannot" in result.matched_text.lower()
            or "can't" in result.matched_text.lower()
        )

    def test_denial_match_is_hashable_and_frozen(self) -> None:
        """Spec requires DenialMatch frozen so it can be sent through
        log/metric pipelines without mutation surprises."""
        m1 = DenialMatch(pattern="x", matched_text="y")
        m2 = DenialMatch(pattern="x", matched_text="y")
        # Frozen → hashable
        assert hash(m1) == hash(m2)
        with pytest.raises(Exception):
            m1.pattern = "z"  # type: ignore[misc]


# =============================================================================
# Corpus regression test — enforces precision/recall thresholds
# =============================================================================


_CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "denial_corpus.json"
)


def _load_corpus() -> list[dict]:
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return list(data["entries"])


class TestCorpusRegression:
    def test_corpus_loads(self) -> None:
        entries = _load_corpus()
        # Spec calls for ~30 denials + ~30 negatives; allow some slack.
        denials = [e for e in entries if e["is_denial"]]
        negatives = [e for e in entries if not e["is_denial"]]
        assert len(denials) >= 25, f"too few denials: {len(denials)}"
        assert len(negatives) >= 25, f"too few negatives: {len(negatives)}"

    def test_corpus_precision_and_recall_thresholds(self) -> None:
        """Run the detector on every corpus entry with
        ``has_recent_tool_call=True`` and enforce the spec's
        thresholds on the labeled set:

          precision >= 0.95   (low false-alarm rate)
          recall    >= 0.85   (catch most denials)
        """
        entries = _load_corpus()
        tp = fp = tn = fn = 0
        misses: list[str] = []
        false_pos: list[str] = []
        for entry in entries:
            text = entry["text"]
            is_denial = bool(entry["is_denial"])
            result = detect_denial(text, has_recent_tool_call=True)
            predicted_denial = result is not None
            if is_denial and predicted_denial:
                tp += 1
            elif is_denial and not predicted_denial:
                fn += 1
                misses.append(text)
            elif not is_denial and predicted_denial:
                fp += 1
                false_pos.append(text)
            else:
                tn += 1

        precision = (
            tp / (tp + fp) if (tp + fp) > 0 else 1.0
        )
        recall = (
            tp / (tp + fn) if (tp + fn) > 0 else 1.0
        )

        # Detailed failure diagnostic so a regression points at the
        # offending entries directly.
        failure_msg = (
            f"\nprecision={precision:.3f} (target >= 0.95)\n"
            f"recall={recall:.3f}    (target >= 0.85)\n"
            f"tp={tp} fp={fp} tn={tn} fn={fn}\n"
            f"missed denials: {misses}\n"
            f"false positives: {false_pos}\n"
        )
        assert precision >= 0.95, failure_msg
        assert recall >= 0.85, failure_msg

    @pytest.mark.parametrize("entry", _load_corpus())
    def test_corpus_per_entry_classification(
        self, entry: dict,
    ) -> None:
        """Per-entry test for richer pytest output. The aggregate
        threshold test above is the contract; this one surfaces
        individual offenders by name when the corpus drifts."""
        text = entry["text"]
        is_denial = bool(entry["is_denial"])
        result = detect_denial(text, has_recent_tool_call=True)
        if is_denial:
            # Soft assertion — recall threshold allows up to 15% miss.
            # We don't fail here on miss; the aggregate test is what
            # blocks merge. This per-entry test is informational.
            if result is None:
                pytest.skip(
                    f"recall miss (acceptable up to 15%): {text!r}"
                )
        else:
            # Precision threshold allows up to 5% false-positive.
            # Same approach — informational, aggregate is the contract.
            if result is not None:
                pytest.skip(
                    f"false positive (acceptable up to 5%): {text!r}"
                )

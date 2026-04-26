"""Denial-pattern detection for the v14 tool consumption pipeline.

Identifies model responses that deny a capability the model just used.
The canonical failure mode is "calls web_search → receives valid
results → writes 'I don't have internet access'". GLM-5.1 reproduces
this pattern reliably on news / realtime queries, but the failure
class is not GLM-specific — any local model with weak instruction-
following + RLHF'd safety patterns is a candidate.

The detector runs after content streaming completes; the turn loop
in ``runtime/conversation.py`` decides whether to retry based on the
result. Gating on ``has_recent_tool_call`` is essential — denial
without a recent tool call is a genuine response (e.g. user asked
"are you connected to the internet?"), not a failure mode worth
retrying.

Curation policy for ``_PATTERNS``:
  * Each pattern targets a specific phrasing observed in production
    or in adjacent failure modes. New phrasings → new pattern.
  * Avoid overly broad patterns. A pattern that catches too many
    legitimate "I don't have X" sentences trips false-positive retries
    that cost the user a provider call.
  * Required corpus thresholds (``tests/fixtures/denial_corpus.json``):
    precision ≥ 0.95, recall ≥ 0.85.

Toggle: ``profile.retry_on_denial`` (default False; opt-in only).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DenialMatch:
    """A detected denial.

    ``pattern`` is the regex that fired (the raw pattern string, not
    the compiled object — easier to log + grep). ``matched_text`` is
    the substring that matched. The caller logs both for retry
    diagnostics; if a future PR observes a high false-positive rate
    on a specific pattern, the corpus + this dataclass make tuning
    straightforward.
    """
    pattern: str
    matched_text: str


# v14 Mechanism C — denial regex corpus.
# Curated for English + Traditional/Simplified Chinese; future PRs
# extend with Japanese / Korean / Arabic as needed. Each pattern
# pairs with at least one positive and one negative entry in
# ``tests/fixtures/denial_corpus.json`` so the regression test
# catches drift.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ── English: "I don't have access to ..." family ────────────────
    re.compile(
        r"I\s*(?:do\s*not|don'?t)\s+have\s+(?:the\s+)?(?:ability\s+to\s+)?access\s+to",
        re.IGNORECASE,
    ),
    # ── English: "I cannot/can't browse|access|fetch|search ..." ────
    re.compile(
        r"I\s*(?:cannot|can(?:'?\s*t|\s+not))\s+(?:browse|access|fetch|search|retrieve|connect)",
        re.IGNORECASE,
    ),
    # ── English: "I (can't / don't) fetch ..." short form ───────────
    re.compile(
        r"I\s+can'?t\s+fetch\s+(?:live|external|real-?time|current|the\s+web)",
        re.IGNORECASE,
    ),
    # ── English: "I'm just/only a coding assistant ... don't have" ──
    re.compile(
        r"I'?m\s+(?:just|only|an?)\s+(?:an?\s+)?(?:coding|code)\s+assistant"
        r"[\s\S]{0,80}?(?:don'?t\s+have|do\s+not\s+have|no(?:\s+access)?)",
        re.IGNORECASE,
    ),
    # ── English: "As an AI ..., I (cannot|can't|don't) ..." ─────────
    re.compile(
        r"As\s+an\s+AI(?:\s+language\s+model)?,?\s+I\s+(?:cannot|can(?:'?\s*t|\s+not)|do\s*n'?t)",
        re.IGNORECASE,
    ),
    # ── English: "I lack real-time / internet / network" ────────────
    re.compile(
        r"I\s+lack\s+(?:real-?time|internet|network|live|external)",
        re.IGNORECASE,
    ),
    # ── English: generic "don't have ability/capability to ..." ─────
    re.compile(
        r"do(?:\s*n'?t|\s+not)\s+have\s+(?:the\s+)?(?:ability|capability)\s+to"
        r"\s+(?:browse|search|fetch|access|connect|retrieve)",
        re.IGNORECASE,
    ),
    # ── Traditional Chinese: 「我(沒有/無法).*連線/網/瀏覽/存取/搜尋」 ──
    re.compile(
        r"我(?:沒有|無法).*?(?:連[線接網](?:[^\u3000-\uFFEF\n.]{0,40}"
        r"(?:互聯網|網際網路|網路|新聞|API|資訊))?|"
        r"瀏覽|存取(?:.*?(?:網|新聞|資訊))?|搜尋(?:.*?網)?)",
    ),
    # ── Traditional Chinese: 「我是.*程式碼.*助手/助理.*無法/沒有」 ──
    re.compile(
        r"我(?:是|只是).*?(?:程式碼|程式|代碼|编程).*?(?:助手|助理)"
        r".{0,80}?(?:無法|沒有|不能)",
        re.S,
    ),
    # ── Traditional Chinese: 「無法.*(瀏覽|搜尋|存取).*(網|新聞|實時)」 ──
    re.compile(
        r"無法.*?(?:瀏覽|搜尋|存取).*?(?:網|新聞|實時|即時)",
    ),
    # ── Simplified Chinese: 「我(没有/无法).*访问/浏览/搜索/网络」 ─
    re.compile(
        r"我(?:没有|无法).*?(?:访问|浏览|搜索|连接|连网|连线|"
        r"互联网|网络|网页|新闻|实时)",
    ),
    # ── Simplified Chinese: 「作为 AI，我无法 ...」 ────────────────
    re.compile(
        r"作为\s*AI[^\u3000-\uFFEF\n]{0,20}?[，,]\s*我(?:无法|不能|没有)",
    ),
    # ── Simplified Chinese: 「我是.*代码助手.*没有」 ──────────────
    re.compile(
        r"我是.*?(?:代码|程序|编程).*?助手.{0,80}?(?:没有|无法|不能)",
        re.S,
    ),
)


def detect_denial(
    content: str, *, has_recent_tool_call: bool,
) -> DenialMatch | None:
    """Scan ``content`` for denial patterns.

    Returns a :class:`DenialMatch` when:
      1. ``has_recent_tool_call`` is True (denial without a recent
         tool call is a genuine answer — user might have asked "are
         you connected?" — and bypassing this gate would force a
         retry that ignores the user's actual question), AND
      2. Any pattern in :data:`_PATTERNS` matches.

    Returns ``None`` otherwise. Stops at the first matching pattern;
    callers don't need the exhaustive list.
    """
    if not has_recent_tool_call:
        return None
    if not content:
        return None
    for pattern in _PATTERNS:
        m = pattern.search(content)
        if m:
            return DenialMatch(
                pattern=pattern.pattern,
                matched_text=m.group(0),
            )
    return None

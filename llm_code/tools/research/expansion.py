"""Multi-query expansion for the v2.8.0 research pipeline (M2).

Given a single user query, expand it into 1-3 sub-queries that hit
different angles of the same intent. M5's research tool feeds each
sub-query through the auto-fallback search chain, dedupes URLs, then
reranks the union.

Two strategies:

* **Template** (default, free) — pattern-rule expansion. ~5 rules
  cover ``research X``, ``X vs Y``, time-sensitive triggers, how-to,
  what-is. CJK trigger words are included so Chinese-language queries
  also expand.
* **LLM** (opt-in) — single round-trip via ``profile.tier_c_model``
  asking for a JSON array of 2-3 alternate phrasings. Falls back to
  template on parse error.

The original query is always element 0 of the returned tuple so the
pipeline degrades gracefully if expansion fails — at worst we still
search the original query.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m2-multi-query-expansion.md
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# CJK trigger words mirror v2.3.1's ``_TIME_SENSITIVE_TRIGGERS`` so a
# Chinese-language ask like "今日 X" expands the same way an English
# "today X" does.
_TIME_SENSITIVE_TRIGGERS_EN: tuple[str, ...] = (
    "today", "latest", "current", "breaking", "right now",
)
_TIME_SENSITIVE_TRIGGERS_CJK: tuple[str, ...] = (
    "今日", "今天", "現在", "即時", "最新",
)
_TIME_SENSITIVE_TRIGGERS = _TIME_SENSITIVE_TRIGGERS_EN + _TIME_SENSITIVE_TRIGGERS_CJK

# Comparison triggers — both English ``vs`` and CJK ``比較`` / 對比.
_COMPARISON_TRIGGERS = (" vs ", " vs. ", "比較", "對比", "比对")

# How-to triggers.
_HOWTO_TRIGGERS_EN = ("how to ", "how do i ", "how can i ")
_HOWTO_TRIGGERS_CJK = ("如何", "怎麼", "怎么", "教學", "教程")
_HOWTO_TRIGGERS = _HOWTO_TRIGGERS_EN + _HOWTO_TRIGGERS_CJK

# What-is triggers.
_WHATIS_TRIGGERS_EN = ("what is ", "what are ", "what's ")
_WHATIS_TRIGGERS_CJK = ("什麼是", "什么是", "是什麼", "是什么")
_WHATIS_TRIGGERS = _WHATIS_TRIGGERS_EN + _WHATIS_TRIGGERS_CJK


def _matches_any(query_lower: str, triggers: tuple[str, ...]) -> bool:
    return any(t in query_lower for t in triggers)


def _strip_research_prefix(query: str) -> str | None:
    """Return the topic if the query starts with 'research X', else None."""
    m = re.match(r"^\s*research\s+(.+)\s*$", query, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _split_vs(query: str) -> tuple[str, str] | None:
    """Return ``(left, right)`` if the query contains a ``vs`` separator."""
    for sep in (" vs. ", " vs ", "比較", "對比"):
        if sep in query:
            parts = query.split(sep, 1)
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                if left and right:
                    return left, right
            break
    return None


def expand_template(query: str, *, max_subqueries: int = 3) -> tuple[str, ...]:
    """Expand ``query`` using template rules.

    Always returns at least ``(query,)`` — original first. Subsequent
    elements are deduplicated case-insensitively and capped at
    ``max_subqueries``.
    """
    if max_subqueries <= 0:
        return (query,)

    out: list[str] = [query]
    seen_lower: set[str] = {query.lower().strip()}

    def _push(candidate: str) -> None:
        candidate = candidate.strip()
        if not candidate:
            return
        key = candidate.lower()
        if key in seen_lower:
            return
        seen_lower.add(key)
        out.append(candidate)

    query_lower = query.lower()

    # Rule 1 — "research X" → ["X paper 2024", "X tutorial"]
    topic = _strip_research_prefix(query)
    if topic:
        _push(f"{topic} paper 2024")
        _push(f"{topic} tutorial")

    # Rule 2 — "A vs B" → ["A comparison", "B comparison"]
    parts = _split_vs(query)
    if parts:
        left, right = parts
        _push(f"{left} comparison")
        _push(f"{right} comparison")

    # Rule 3 — time-sensitive trigger → add "<query> today" + "<query> news".
    if _matches_any(query_lower, _TIME_SENSITIVE_TRIGGERS):
        _push(f"{query} news")
        _push(f"{query} update")

    # Rule 4 — how-to → add "<topic> tutorial" / "<topic> guide".
    if _matches_any(query_lower, _HOWTO_TRIGGERS):
        # Trim common how-to prefixes for cleaner sub-queries.
        topic_text = query
        for prefix in _HOWTO_TRIGGERS_EN + _HOWTO_TRIGGERS_CJK:
            idx = query_lower.find(prefix)
            if idx >= 0:
                topic_text = query[idx + len(prefix):].strip()
                break
        if topic_text:
            _push(f"{topic_text} tutorial")
            _push(f"{topic_text} guide")

    # Rule 5 — what-is → add "<topic> definition" / "<topic> explained".
    if _matches_any(query_lower, _WHATIS_TRIGGERS):
        topic_text = query
        for prefix in _WHATIS_TRIGGERS_EN + _WHATIS_TRIGGERS_CJK:
            idx = query_lower.find(prefix)
            if idx >= 0:
                topic_text = query[idx + len(prefix):].strip().rstrip("?").strip()
                break
        if topic_text:
            _push(f"{topic_text} definition")
            _push(f"{topic_text} explained")

    return tuple(out[:max_subqueries])


async def _llm_expand(
    query: str,
    profile: Any,
    *,
    max_subqueries: int = 3,
    provider: Any = None,
    model: str | None = None,
) -> tuple[str, ...]:
    """LLM-driven expansion via ``profile.tier_c_model``.

    Sends a single fixed-prompt request asking for a JSON array of 2-3
    alternate phrasings; parse failure or empty array falls back to
    the template strategy. The original query is always returned as
    element 0.

    Args:
        query: User query.
        profile: ``ModelProfile`` providing ``tier_c_model``.
        max_subqueries: Cap on the returned tuple.
        provider: Optional provider for tests / dependency injection.
            When ``None``, no LLM call is attempted (real provider
            wiring is the caller's responsibility — the M5 pipeline
            owns provider construction).
        model: Optional explicit model override. Defaults to
            ``profile.tier_c_model``.
    """
    if max_subqueries <= 0:
        return (query,)
    if provider is None:
        # No provider injected — degrade gracefully to template.
        return expand_template(query, max_subqueries=max_subqueries)

    target_model = model or getattr(profile, "tier_c_model", "")
    if not target_model:
        return expand_template(query, max_subqueries=max_subqueries)

    system_prompt = (
        "You expand a user query into 2-3 alternative phrasings for diverse "
        "search results. Return ONLY a JSON array of strings — no commentary."
    )
    user_prompt = f"User query: {query}\n\nReturn JSON array now:"

    try:
        from llm_code.api.types import Message, MessageRequest, TextBlock
        sys_msg = Message(role="system", content=(TextBlock(text=system_prompt),))
        user_msg = Message(role="user", content=(TextBlock(text=user_prompt),))
        request = MessageRequest(
            model=target_model,
            messages=(sys_msg, user_msg),
            max_tokens=256,
            temperature=0.0,
            stream=False,
        )
        response = await provider.send_message(request)
        raw = response.content[0].text if response.content else ""
        # Find the first ``[ ... ]`` block; tolerate thinking-channel
        # noise around it.
        m = re.search(r"\[.*?\]", raw, flags=re.DOTALL)
        if not m:
            raise ValueError("no JSON array in response")
        parsed = json.loads(m.group(0))
        if not isinstance(parsed, list):
            raise ValueError("not a list")
        candidates = [str(x).strip() for x in parsed if isinstance(x, str) and x.strip()]
    except Exception as exc:
        logger.info(
            "LLM query expansion failed (%s); falling back to template", exc,
        )
        return expand_template(query, max_subqueries=max_subqueries)

    out: list[str] = [query]
    seen_lower = {query.lower().strip()}
    for c in candidates:
        key = c.lower().strip()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(c)
    return tuple(out[:max_subqueries])


async def expand(
    query: str,
    profile: Any,
    *,
    provider: Any = None,
    model: str | None = None,
) -> tuple[str, ...]:
    """Dispatch on ``profile.research_query_expansion``.

    Modes:
        ``"off"``      → ``(query,)``
        ``"template"`` → :func:`expand_template`
        ``"llm"``      → :func:`_llm_expand` with template fallback
    """
    mode = (
        getattr(profile, "research_query_expansion", "template") or "template"
    )
    cap = int(getattr(profile, "research_max_subqueries", 3))
    if mode == "off":
        return (query,)
    if mode == "llm":
        return await _llm_expand(
            query, profile,
            max_subqueries=cap,
            provider=provider,
            model=model,
        )
    # Default + safe fallback.
    return expand_template(query, max_subqueries=cap)

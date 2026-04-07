"""3-tier skill router: keyword → TF-IDF → LLM classifier.

Routes user messages to the most relevant auto-skill(s) so only
matched skill content is injected into the system prompt.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from llm_code.runtime._stopwords import STOPWORDS
from llm_code.runtime.config import SkillRouterConfig

logger = logging.getLogger(__name__)

# Unicode ranges for CJK characters
_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0xFF66, 0xFF9F),    # Halfwidth Katakana
    (0xAC00, 0xD7AF),    # Hangul Syllables
)



# ------------------------------------------------------------------
# Tokenisation
# ------------------------------------------------------------------

def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase tokens, handling CJK characters.

    Latin words are split on whitespace/punctuation.
    CJK characters are emitted individually (each is a token).
    """
    tokens: list[str] = []
    buf: list[str] = []

    for ch in text:
        if _is_cjk(ch):
            # Flush Latin buffer
            if buf:
                word = "".join(buf).lower()
                if word:
                    tokens.append(word)
                buf.clear()
            tokens.append(ch)
        elif ch.isalnum() or ch in ("_", "-"):
            buf.append(ch)
        else:
            if buf:
                word = "".join(buf).lower()
                if word:
                    tokens.append(word)
                buf.clear()

    if buf:
        word = "".join(buf).lower()
        if word:
            tokens.append(word)

    return tokens


def _content_tokens(text: str) -> list[str]:
    """Tokenize and remove stopwords."""
    return [t for t in tokenize(text) if t not in STOPWORDS]


# ------------------------------------------------------------------
# Keyword extraction
# ------------------------------------------------------------------

def _extract_keywords(skill: Any) -> frozenset[str]:
    """Extract routing keywords from a skill.

    Sources (priority order):
    1. Explicit ``keywords`` tuple from SKILL.md frontmatter
    2. Auto-extracted content words from name + description + tags
    """
    kws: set[str] = set()

    # Explicit keywords
    for kw in getattr(skill, "keywords", ()):
        kws.update(tokenize(str(kw)))

    # Auto-extract from description + name + tags
    text = f"{skill.name} {skill.description} {' '.join(getattr(skill, 'tags', ()))}"
    for tok in _content_tokens(text):
        if len(tok) >= 2:  # skip single-char tokens from descriptions
            kws.add(tok)

    return frozenset(kws)


# ------------------------------------------------------------------
# TF-IDF
# ------------------------------------------------------------------

@dataclass
class _TfidfIndex:
    """Pre-computed TF-IDF vectors for a small skill corpus."""

    idf: dict[str, float] = field(default_factory=dict)
    vectors: dict[str, dict[str, float]] = field(default_factory=dict)  # skill_name → term→weight


def _build_tfidf_index(skills: Sequence[Any]) -> _TfidfIndex:
    """Build a TF-IDF index from skill descriptions."""
    # Collect documents (one per skill)
    docs: dict[str, list[str]] = {}
    for skill in skills:
        text = f"{skill.name} {skill.description} {' '.join(getattr(skill, 'tags', ()))}"
        docs[skill.name] = _content_tokens(text)

    # IDF: log(N / df)
    n = len(docs)
    if n == 0:
        return _TfidfIndex()
    df: Counter[str] = Counter()
    for tokens in docs.values():
        df.update(set(tokens))
    idf = {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}

    # TF-IDF vectors
    vectors: dict[str, dict[str, float]] = {}
    for name, tokens in docs.items():
        if not tokens:
            vectors[name] = {}
            continue
        tf = Counter(tokens)
        total = len(tokens)
        vec = {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}
        vectors[name] = vec

    return _TfidfIndex(idf=idf, vectors=vectors)


def _tfidf_query_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector for a query."""
    if not tokens:
        return {}
    tf = Counter(tokens)
    total = len(tokens)
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors."""
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in a if k in b)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ------------------------------------------------------------------
# Tier C: LLM classifier
# ------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You are an intent classifier. Given the skill list below, reply with ONLY \
the skill name that best matches the user's request. If none match, reply "none".
Do not explain.

Skills:
{skill_list}

User request: {user_message}
Skill name:"""


async def _classify_with_llm(
    user_message: str,
    skills: Sequence[Any],
    provider: Any,
    model: str,
) -> str | None:
    """Ask the LLM to classify user intent. Returns skill name or None."""
    skill_list = "\n".join(f"- {s.name}: {s.description[:80]}" for s in skills)
    prompt = _CLASSIFY_PROMPT.format(skill_list=skill_list, user_message=user_message)

    try:
        from llm_code.api.types import MessageRequest, Message, TextBlock
        user_msg = Message(role="user", content=(TextBlock(text=prompt),))
        request = MessageRequest(
            model=model,
            messages=(user_msg,),
            max_tokens=20,
            temperature=0.0,
            stream=False,
        )
        response = await provider.send_message(request)
        answer = response.content[0].text.strip().lower() if response.content else ""
        skill_names = {s.name.lower(): s.name for s in skills}
        if answer in skill_names:
            return skill_names[answer]
        return None
    except Exception:
        logger.debug("Tier C classification failed", exc_info=True)
        return None


# ------------------------------------------------------------------
# SkillRouter
# ------------------------------------------------------------------

class SkillRouter:
    """3-tier cascade skill router.

    Tier A: Keyword matching (0ms)
    Tier B: TF-IDF similarity (~10ms)
    Tier C: LLM classifier (2-5s, optional)
    """

    def __init__(
        self,
        skills: Sequence[Any],
        config: SkillRouterConfig | None = None,
        provider: Any = None,
        model: str = "",
    ) -> None:
        from llm_code.runtime.config import SkillRouterConfig as _SRC
        self._config = config or _SRC()
        self._skills = tuple(skills)
        self._provider = provider
        self._model = model

        # Pre-compute Tier A: keyword index
        self._keyword_index: dict[str, list[Any]] = {}
        self._skill_keywords: dict[str, frozenset[str]] = {}
        for skill in self._skills:
            kws = _extract_keywords(skill)
            self._skill_keywords[skill.name] = kws
            for kw in kws:
                self._keyword_index.setdefault(kw, []).append(skill)

        # Pre-compute Tier B: TF-IDF index
        self._tfidf = _build_tfidf_index(self._skills)

        # Cache for route results
        self._cache: dict[str, list[Any]] = {}
        self._cache_max = 128

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, user_message: str) -> list[Any]:
        """Route a user message to matching skills (sync, Tier A + B only)."""
        if not self._config.enabled or not self._skills:
            return []

        # Cache check
        cache_key = user_message[:200]
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._tier_a(user_message)
        if not result and self._config.tier_b:
            result = self._tier_b(user_message)

        result = result[: self._config.max_skills_per_turn]

        # Update cache
        if len(self._cache) >= self._cache_max:
            self._cache.clear()
        self._cache[cache_key] = result

        return result

    async def route_async(self, user_message: str) -> list[Any]:
        """Route with all 3 tiers (async, includes optional Tier C).

        Tier C is consulted when:
          1. Tier A/B both miss, AND
          2. Either ``config.tier_c`` is True, OR
             ``config.tier_c_auto_for_cjk`` is True and the message contains CJK.

        The CJK auto-fallback exists because skill descriptions are typically
        English; keyword/TF-IDF matching can't bridge the language gap, but an
        LLM classifier can judge intent semantically.
        """
        result = self.route(user_message)
        if result:
            return result

        # Decide whether Tier C should fire
        tier_c_enabled = self._config.tier_c
        if not tier_c_enabled and self._config.tier_c_auto_for_cjk:
            tier_c_enabled = any(_is_cjk(ch) for ch in user_message)

        # Tier C fallback
        if tier_c_enabled and self._provider:
            model = self._config.tier_c_model or self._model
            name = await _classify_with_llm(
                user_message, self._skills, self._provider, model,
            )
            if name:
                for skill in self._skills:
                    if skill.name == name:
                        result = [skill]
                        # Cache the LLM result
                        cache_key = user_message[:200]
                        self._cache[cache_key] = result
                        return result

        return []

    # ------------------------------------------------------------------
    # Tier A: Keyword matching
    # ------------------------------------------------------------------

    def _tier_a(self, user_message: str) -> list[Any]:
        if not self._config.tier_a:
            return []

        msg_tokens = set(tokenize(user_message))

        # Score each skill by keyword overlap
        scores: dict[str, int] = {}
        for skill in self._skills:
            kws = self._skill_keywords[skill.name]
            overlap = msg_tokens & kws
            if overlap:
                scores[skill.name] = len(overlap)

        if not scores:
            return []

        # Return skills with score >= 2, or the top-1 if only score-1 matches
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        threshold = 2
        result = [name for name, score in ranked if score >= threshold]

        if not result:
            # Single keyword match — only return if it's a strong signal
            top_name, top_score = ranked[0]
            if top_score >= 1:
                result = [top_name]

        # Map names back to Skill objects
        name_to_skill = {s.name: s for s in self._skills}
        return [name_to_skill[n] for n in result if n in name_to_skill]

    # ------------------------------------------------------------------
    # Tier B: TF-IDF similarity
    # ------------------------------------------------------------------

    def _tier_b(self, user_message: str) -> list[Any]:
        if not self._config.tier_b:
            return []

        query_tokens = _content_tokens(user_message)
        query_vec = _tfidf_query_vector(query_tokens, self._tfidf.idf)
        if not query_vec:
            return []

        similarities: list[tuple[str, float]] = []
        for skill_name, skill_vec in self._tfidf.vectors.items():
            sim = _cosine_similarity(query_vec, skill_vec)
            if sim >= self._config.similarity_threshold:
                similarities.append((skill_name, sim))

        if not similarities:
            return []

        similarities.sort(key=lambda x: -x[1])
        name_to_skill = {s.name: s for s in self._skills}
        return [name_to_skill[n] for n, _ in similarities if n in name_to_skill]

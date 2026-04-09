"""3-tier skill router: keyword → TF-IDF → LLM classifier.

Routes user messages to the most relevant auto-skill(s) so only
matched skill content is injected into the system prompt.
"""
from __future__ import annotations

import asyncio
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
You are an intent classifier. Reply with ONLY the skill name on a single line.
No thinking, no explanation, no markdown, no analysis. Just the bare skill name.
If none match, reply: none

Skills:
{skill_list}

User request: {user_message}

/no_think
Answer:"""


async def _classify_with_llm_debug(
    user_message: str,
    skills: Sequence[Any],
    provider: Any,
    model: str,
) -> tuple[str | None, str]:
    """Ask the LLM to classify user intent. Returns (skill_name, raw_answer)."""
    skill_list = "\n".join(f"- {s.name}: {s.description[:80]}" for s in skills)
    prompt = _CLASSIFY_PROMPT.format(skill_list=skill_list, user_message=user_message)

    try:
        from llm_code.api.types import MessageRequest, Message, TextBlock
        user_msg = Message(role="user", content=(TextBlock(text=prompt),))
        request = MessageRequest(
            model=model,
            messages=(user_msg,),
            max_tokens=512,  # generous: thinking models burn tokens before answering
            temperature=0.0,
            stream=False,
        )
        response = await provider.send_message(request)
        raw = response.content[0].text if response.content else ""
        skill_names = {s.name.lower(): s.name for s in skills}

        # 1) Try clean answer extraction: strip thinking blocks, take first line
        cleaned = raw
        for tag in ("</think>", "</thinking>", "Answer:", "answer:"):
            if tag in cleaned:
                cleaned = cleaned.rsplit(tag, 1)[-1]
        first_line = ""
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
        answer = first_line.strip().lower().strip(" .,:;!?\"'`*[](){}\n\t")
        if answer == "none":
            # Authoritative no-match: do NOT fall through to substring fallback.
            return None, raw
        if answer and answer in skill_names:
            return skill_names[answer], raw

        # 2) Fallback: scan the FULL raw text (including thinking) for any
        # skill name as a substring. Reasoning models often discuss the
        # candidate skills by name in their thinking block — that's good
        # enough signal even if no clean answer line was emitted.
        # Require >=2 occurrences AND margin >=2 over runner-up. A single
        # mention is too weak because reasoning models mention all candidates
        # while ruling them out.
        _MIN_MENTIONS = 2
        _MIN_MARGIN = 2
        raw_lower = raw.lower()
        scores = {
            name: raw_lower.count(lname)
            for lname, name in skill_names.items()
        }
        scores = {n: c for n, c in scores.items() if c >= _MIN_MENTIONS}
        if scores:
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            best_name, best_score = ranked[0]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0
            if best_score - runner_up >= _MIN_MARGIN:
                return best_name, raw

        return None, raw
    except Exception as e:
        logger.debug("Tier C classification failed", exc_info=True)
        return None, f"<exception: {e!r}>"


async def _classify_with_llm(
    user_message: str,
    skills: Sequence[Any],
    provider: Any,
    model: str,
) -> str | None:
    """Backward-compat wrapper around _classify_with_llm_debug."""
    name, _ = await _classify_with_llm_debug(user_message, skills, provider, model)
    return name


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
        # Debug: last Tier C trace (for TUI surfacing)
        self.last_tier_c_debug: str = ""
        # Tracks which tier produced the most recent match: "a" | "b" | "c" | "".
        self.last_tier_used: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_skill(self, skill: Any) -> None:
        """Wave2-5: register a skill after construction time.

        Used by the plugin executor when a plugin declares skills —
        we don't want to rebuild the router from scratch just to add
        one entry. The router's caches are invalidated so subsequent
        route() calls see the new skill immediately.

        Raises ValueError if a skill with the same name is already
        registered, so plugin name conflicts are caught loudly
        instead of silently shadowing the built-in skill.
        """
        for existing in self._skills:
            if getattr(existing, "name", None) == skill.name:
                raise ValueError(
                    f"Skill '{skill.name}' is already registered; "
                    f"remove the existing entry before adding a plugin skill"
                )
        self._skills = (*self._skills, skill)
        # Tier A: add the new skill to the keyword index
        kws = _extract_keywords(skill)
        self._skill_keywords[skill.name] = kws
        for kw in kws:
            self._keyword_index.setdefault(kw, []).append(skill)
        # Tier B: rebuild the TF-IDF index from the new skill list.
        # Incremental TF-IDF updates are possible but not worth the
        # complexity for a handful of plugin skills — a rebuild is
        # cheap for <100 skills which is the realistic ceiling.
        self._tfidf = _build_tfidf_index(self._skills)
        # Invalidate cached route results — an old answer might have
        # missed a better plugin match now that the skill exists.
        self._cache.clear()

    def remove_skill(self, name: str) -> bool:
        """Wave2-5: un-register a skill by name.

        Returns True on removal, False if the name was not found.
        Called when a plugin is disabled or unloaded.
        """
        new_skills = tuple(s for s in self._skills if getattr(s, "name", None) != name)
        if len(new_skills) == len(self._skills):
            return False
        self._skills = new_skills
        # Rebuild both indices from scratch — removing a skill from
        # the keyword index would require scanning every bucket,
        # which is the same cost as rebuilding.
        self._skill_keywords.pop(name, None)
        self._keyword_index = {}
        for existing in self._skills:
            for kw in self._skill_keywords.get(existing.name, frozenset()):
                self._keyword_index.setdefault(kw, []).append(existing)
        self._tfidf = _build_tfidf_index(self._skills)
        self._cache.clear()
        return True

    def route(self, user_message: str) -> list[Any]:
        """Route a user message to matching skills (sync, Tier A + B only)."""
        if not self._config.enabled or not self._skills:
            return []

        # Cache check
        cache_key = user_message[:200]
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._tier_a(user_message)
        if result:
            self.last_tier_used = "a"
        elif self._config.tier_b:
            result = self._tier_b(user_message)
            if result:
                self.last_tier_used = "b"
        if not result:
            self.last_tier_used = ""

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

        Caches BOTH positive AND negative results. The negative cache
        is critical because ``route_async`` is called twice per turn
        (once from the TUI for display at ``app.py:1426``, once from
        the conversation runtime for prompt injection at
        ``conversation.py:1036``). Without negative caching, the
        5-15s Tier C LLM round-trip runs TWICE per CJK turn.
        """
        import time
        _t0 = time.monotonic()

        self.last_tier_c_debug = ""

        # Check cache FIRST including negative results. The sync
        # ``route`` below also checks the cache but its own fast
        # path assumes a hit means "tier A/B matched positively"
        # — our negative-hit shortcut here returns the cached []
        # and skips the Tier C decision tree entirely.
        cache_key = user_message[:200]
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            logger.debug(
                "skill_router cache hit: %d skills in %.3fs",
                len(cached), time.monotonic() - _t0,
            )
            return cached

        # Persistent cross-session cache lookup. Same query after
        # a TUI restart returns instantly instead of re-running
        # the 14s Tier C LLM classifier.
        try:
            from llm_code.runtime import skill_router_cache as _src
            _cached_match = _src.load_cached_match(
                user_message=user_message,
                skill_names=[s.name for s in self._skills],
            )
            if _cached_match is not _src.NOT_CACHED:
                if _cached_match is None:
                    result_from_disk: list[Any] = []
                    _populate = True
                else:
                    result_from_disk = [
                        s for s in self._skills if s.name == _cached_match
                    ]
                    # If the cached skill name no longer exists
                    # (e.g. user deleted it between sessions), fall
                    # through to live lookup. The hash guard usually
                    # prevents this but belt-and-braces.
                    _populate = bool(result_from_disk)

                if _populate:
                    # Populate in-memory cache so subsequent same-
                    # session calls skip the disk read.
                    self._cache[cache_key] = result_from_disk
                    logger.debug(
                        "skill_router persistent cache hit: %d skills "
                        "in %.3fs (matched=%r)",
                        len(result_from_disk),
                        time.monotonic() - _t0,
                        _cached_match,
                    )
                    return result_from_disk
        except Exception as exc:
            logger.debug("skill_router_cache read failed: %s", exc)

        result = self.route(user_message)
        if result:
            logger.debug(
                "skill_router tier_%s: %d skills in %.3fs",
                self.last_tier_used, len(result), time.monotonic() - _t0,
            )
            return result

        # Decide whether Tier C should fire
        tier_c_enabled = self._config.tier_c
        has_cjk = any(_is_cjk(ch) for ch in user_message)
        if not tier_c_enabled and self._config.tier_c_auto_for_cjk:
            tier_c_enabled = has_cjk

        self.last_tier_c_debug = (
            f"AB-miss cjk={has_cjk} tc_enabled={tier_c_enabled} "
            f"provider={self._provider is not None}"
        )

        # Tier C fallback
        if tier_c_enabled and self._provider:
            _tc_start = time.monotonic()
            model = self._config.tier_c_model or self._model
            _tc_timeout = getattr(self._config, "tier_c_timeout", 15.0)
            logger.debug(
                "skill_router tier C starting: model=%s cjk=%s timeout=%.0fs",
                model, has_cjk, _tc_timeout,
            )
            try:
                name, raw = await asyncio.wait_for(
                    _classify_with_llm_debug(
                        user_message, self._skills, self._provider, model,
                    ),
                    timeout=_tc_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "skill_router tier_c timed out after %.0fs; skipping",
                    _tc_timeout,
                )
                name, raw = None, ""
            _tc_elapsed = time.monotonic() - _tc_start
            self.last_tier_c_debug += (
                f" model={model!r} raw={raw!r} matched={name!r} "
                f"elapsed={_tc_elapsed:.2f}s"
            )
            logger.debug(
                "skill_router tier_c complete: matched=%r in %.2fs",
                name, _tc_elapsed,
            )
            if name:
                for skill in self._skills:
                    if skill.name == name:
                        result = [skill]
                        self._cache[cache_key] = result
                        self.last_tier_used = "c"
                        # Also persist to disk so the next TUI
                        # session gets this answer for free
                        # instead of re-running the 14s classifier.
                        self._persist_tier_c_result(user_message, name)
                        logger.debug(
                            "skill_router tier_c hit: %d skills total %.3fs",
                            len(result), time.monotonic() - _t0,
                        )
                        return result
            # Cache the NEGATIVE Tier C result. Without this the
            # second route_async call in the same turn re-runs the
            # expensive LLM classifier. Also persist to disk so
            # the next TUI session skips this query's Tier C run.
            self._cache[cache_key] = []
            self._persist_tier_c_result(user_message, None)
            logger.debug(
                "skill_router tier_c miss (negative cached): %.3fs total",
                time.monotonic() - _t0,
            )
            return []

        # No Tier C path available — cache the empty result anyway
        # so the second call skips the AB-miss decision tree.
        self._cache[cache_key] = []
        logger.debug(
            "skill_router no-tier (negative cached): %.3fs total",
            time.monotonic() - _t0,
        )
        return []

    def _persist_tier_c_result(
        self, user_message: str, matched_skill: str | None,
    ) -> None:
        """Best-effort write to persistent cross-session cache.

        Failures are swallowed (logged at DEBUG) — this is a pure
        optimization, not correctness-critical.
        """
        try:
            from llm_code.runtime import skill_router_cache as _src
            _src.save_match(
                user_message=user_message,
                skill_names=[s.name for s in self._skills],
                matched_skill=matched_skill,
            )
        except Exception as exc:
            logger.debug("skill_router_cache write failed: %s", exc)

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

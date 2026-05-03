"""Lightweight Web RAG preflight for local-model knowledge augmentation."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from llm_code.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_EXTERNAL_KNOWLEDGE_RE = re.compile(
    r"("
    r"今日|今天|現在|即時|最新|近期|新聞|熱門|查詢|搜尋|搜索|"
    r"法規|價格|股價|匯率|天氣|版本|更新|release\s+note|"
    r"\btoday\b|\blatest\b|\bcurrent\b|\bbreaking\b|\bnews\b|"
    r"\bsearch\b|\blook\s+up\b|\brelease\b|\bchangelog\b|\bprice\b"
    r")",
    re.IGNORECASE,
)

_CODING_STATIC_RE = re.compile(
    r"("
    r"解這題|演算法|複雜度|重構|修正|實作|程式|"
    r"\bcode\b|\bcoding\b|\brefactor\b|\bimplement\b|\bdebug\b|"
    r"\bdfs\b|\bbfs\b|\bgrid\b|\bo\([^)]+\)"
    r")",
    re.IGNORECASE,
)

_HTTP_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_RESULT_HEADER_RE = re.compile(
    r"^\d+\. \*\*\[(?P<title>.+?)\]\((?P<url>https?://[^)]+)\)\*\*$"
)
_RESULT_COUNT_RE = re.compile(r"^\(\d+ results\)$")


def _extract_http_urls(text: str, *, limit: int) -> list[str]:
    """Extract unique HTTP URLs from markdown/plain search output."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _HTTP_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _is_weak_homepage_result(url: str, snippet: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "/").strip("/").lower()
    if path not in {"", "home", "index.html", "index.htm"}:
        return False
    generic_markers = (
        "provides the latest",
        "匯集",
        "提供最新",
        "即時、多元",
        "最新、最完整",
        "最新、最即時",
        "world's news",
    )
    lower_snippet = snippet.lower()
    return any(marker in lower_snippet for marker in generic_markers)


def _filter_weak_search_results(text: str) -> str:
    """Drop homepage/directory search results that are weak factual evidence."""
    lines = (text or "").splitlines()
    out: list[str] = []
    kept = 0
    i = 0
    while i < len(lines):
        header = _RESULT_HEADER_RE.match(lines[i])
        if header is None:
            if not _RESULT_COUNT_RE.match(lines[i]):
                out.append(lines[i])
            i += 1
            continue

        block = [lines[i]]
        i += 1
        while (
            i < len(lines)
            and _RESULT_HEADER_RE.match(lines[i]) is None
            and _RESULT_COUNT_RE.match(lines[i]) is None
        ):
            block.append(lines[i])
            i += 1

        snippet = " ".join(block[1:])
        if _is_weak_homepage_result(header.group("url"), snippet):
            continue

        kept += 1
        block[0] = f"{kept}. **[{header.group('title')}]({header.group('url')})**"
        out.extend(block)

    if kept:
        out.append(f"({kept} evidence-oriented results)")
    return "\n".join(out).strip()


def _is_low_quality_fetch_output(text: str) -> bool:
    """Return True for raw JS/boilerplate fetch output that is not evidence."""
    lower = (text or "").lower()
    markers = (
        "window.",
        "wiz_global_data",
        "googletag",
        "document.createelement",
        "function ",
        "var ",
        "pubads",
    )
    return sum(1 for marker in markers if marker in lower) >= 3


def should_augment_with_web(text: str) -> bool:
    """Return True when a prompt benefits from external web knowledge.

    The detector deliberately favors fresh/news/search/version/price style
    prompts and avoids pure coding or static CS questions. It is a heuristic,
    not a classifier; the fallback on uncertainty is to avoid an unsolicited
    network call.
    """
    prompt = text or ""
    if not _EXTERNAL_KNOWLEDGE_RE.search(prompt):
        return False
    # "latest llama.cpp server options" is external knowledge even though it
    # contains a technical noun; pure code/problem prompts should stay local.
    if re.search(r"最新|latest|release|changelog|查詢|搜尋|搜索|新聞|news", prompt, re.IGNORECASE):
        return True
    return not _CODING_STATIC_RE.search(prompt)


async def build_web_rag_context(
    user_input: str,
    tool_registry: "ToolRegistry",
    *,
    max_results: int = 10,
    max_fetches: int = 3,
) -> str:
    """Run web_search/web_fetch and format retrieved context for the model."""
    if not should_augment_with_web(user_input):
        return ""

    search_tool = tool_registry.get("web_search")
    if search_tool is None:
        return ""

    try:
        result = await search_tool.execute_async({
            "query": user_input,
            "max_results": max_results,
            "backend": "auto",
        })
    except Exception as exc:  # pragma: no cover - defensive around plugins
        logger.info("web RAG preflight failed: %s", exc)
        return ""

    search_output = _filter_weak_search_results(result.output)
    if result.is_error or not search_output.strip():
        logger.info("web RAG preflight produced no context: %s", result.output[:200])
        return ""

    fetch_sections: list[str] = []
    fetch_tool = tool_registry.get("web_fetch")
    if fetch_tool is not None and max_fetches > 0:
        for index, url in enumerate(_extract_http_urls(search_output, limit=max_fetches), start=1):
            try:
                fetched = await fetch_tool.execute_async({
                    "url": url,
                    "max_length": 6000,
                    "prompt": (
                        "Extract the concrete facts, dates, titles, "
                        "versions, prices, or claims relevant to the "
                        "user request. Keep source context."
                    ),
                })
            except Exception as exc:  # pragma: no cover - defensive
                logger.info("web RAG fetch failed for %s: %s", url, exc)
                continue
            if (
                fetched.is_error
                or not fetched.output.strip()
                or _is_low_quality_fetch_output(fetched.output)
            ):
                continue
            fetch_sections.append(
                f"### Source {index}: {url}\n\n{fetched.output.strip()[:6000]}"
            )

    fetched_context = ""
    if fetch_sections:
        fetched_context = (
            "\n\n## Fetched source excerpts\n\n"
            + "\n\n".join(fetch_sections)
        )

    return (
        "## Web RAG context\n\n"
        "The user request appears to need external or recently updated "
        "knowledge. Use the retrieved context below as grounding. Do not "
        "invent sources, dates, release details, prices, headlines, or facts "
        "that are not supported by these results. If the retrieved context is "
        "insufficient, say that verified information is insufficient and state "
        "what would need to be checked. Prefer fetched source excerpts over "
        "search snippets. Use snippets only as weak leads when no fetched "
        "source excerpt is available. For news/current-event requests, answer "
        "only with concrete items supported by the retrieved context; do not "
        "count a source homepage, index page, or generic directory description "
        "as a news item unless the retrieved text contains a concrete article "
        "title or event. When presenting external facts, cite the source title "
        "or URL for each distinct item.\n\n"
        "## Search result snippets\n\n"
        f"{search_output}"
        f"{fetched_context}"
    )

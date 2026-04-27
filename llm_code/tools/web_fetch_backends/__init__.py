"""Optional web-fetch extraction backends (v2.8.0 M6).

The primary `web_fetch.py` flat module owns the fast paths (Jina
Reader + local readability + httpx + playwright). This sibling
package holds opt-in backends gated by env vars: Firecrawl is the
v2.8.0 entrant.

Plan: docs/superpowers/plans/2026-04-27-llm-code-v17-m6-firecrawl.md
"""
from __future__ import annotations

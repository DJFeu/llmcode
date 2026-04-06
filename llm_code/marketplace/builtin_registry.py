"""Built-in registry of known Claude Code plugins (official + community).

Claude Code's plugin architecture:
  - Official plugins live in the anthropics/claude-plugins-official repo
    - /plugins/{name} — Anthropic-maintained plugins
    - /external_plugins/{name} — third-party plugins accepted into official marketplace
  - Community plugins come from independent repos or other marketplaces
  - superpowers (obra/superpowers) is a standalone plugin, NOT a container for others
"""
from __future__ import annotations

# Marketplace repo that hosts most official plugins
_OFFICIAL_MARKETPLACE = "anthropics/claude-plugins-official"

OFFICIAL_PLUGINS = [
    # ── Standalone repos (not in the official marketplace repo) ──
    {"name": "superpowers", "desc": "Core skills: TDD, debugging, collaboration, plans", "skills": 28, "repo": "obra/superpowers"},
    {"name": "chrome-devtools-mcp", "desc": "Browser automation, debugging, performance analysis", "skills": 8, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/chrome-devtools-mcp"},
    {"name": "figma", "desc": "Figma MCP server + design-to-code skills", "skills": 7, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/figma"},
    # ── Internal plugins (in /plugins/ of the official marketplace) ──
    {"name": "searchfit-seo", "desc": "AI-powered SEO toolkit — audit, content strategy", "skills": 22, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/searchfit-seo"},
    {"name": "data-engineering", "desc": "Data warehouse exploration, pipeline audit, SQL", "skills": 46, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/data-engineering"},
    {"name": "frontend-design", "desc": "UI/UX implementation for web frontends", "skills": 3, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/frontend-design"},
    {"name": "remember", "desc": "Continuous memory — extract and persist context", "skills": 2, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/remember"},
    {"name": "skill-creator", "desc": "Create, improve, and measure skill performance", "skills": 3, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/skill-creator"},
    {"name": "claude-code-setup", "desc": "Analyze codebases, recommend Claude Code automations", "skills": 1, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/claude-code-setup"},
    {"name": "claude-md-management", "desc": "Audit and improve CLAUDE.md files", "skills": 1, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/claude-md-management"},
    {"name": "code-review", "desc": "Automated code review with specialized agents", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/code-review"},
    {"name": "code-simplifier", "desc": "Simplify and refine code for clarity", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/code-simplifier"},
    {"name": "commit-commands", "desc": "Streamline git workflow — commit, push, PR", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/commit-commands"},
    {"name": "feature-dev", "desc": "Feature development workflow with specialized agents", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/feature-dev"},
    {"name": "pr-review-toolkit", "desc": "Comprehensive PR review with specialized agents", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/pr-review-toolkit"},
    {"name": "agent-sdk-dev", "desc": "Claude Agent SDK development tools", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/agent-sdk-dev"},
    {"name": "explanatory-output-style", "desc": "Educational insights about implementation choices", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/explanatory-output-style"},
    {"name": "security-guidance", "desc": "Security reminder hooks for safe coding", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/security-guidance"},
    {"name": "ralph-loop", "desc": "Continuous self-referential AI loops", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/ralph-loop"},
    # ── External plugins (in /external_plugins/ of the official marketplace) ──
    {"name": "context7", "desc": "Context7 MCP for up-to-date documentation lookup", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/context7"},
    {"name": "playwright", "desc": "Browser automation and E2E testing (Microsoft)", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/playwright"},
    {"name": "supabase", "desc": "Supabase MCP for database and auth operations", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/supabase"},
    {"name": "semgrep", "desc": "Semgrep MCP for static analysis and SAST", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/semgrep"},
    # ── LSP integrations ──
    {"name": "clangd-lsp", "desc": "C/C++ language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/clangd-lsp"},
    {"name": "gopls-lsp", "desc": "Go language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/gopls-lsp"},
    {"name": "pyright-lsp", "desc": "Python language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/pyright-lsp"},
    {"name": "rust-analyzer-lsp", "desc": "Rust language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/rust-analyzer-lsp"},
    {"name": "typescript-lsp", "desc": "TypeScript language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/typescript-lsp"},
]

COMMUNITY_PLUGINS = [
    # Verified: exists in known_marketplaces or has standalone repo
    {"name": "codex", "desc": "OpenAI Codex companion — rescue, review, second opinion", "skills": 5, "repo": "openai/codex-plugin-cc"},
    # Third-party from paddo-tools marketplace
    {"name": "gemini-tools", "desc": "Gemini integration for Claude Code", "skills": 0, "repo": "paddo/claude-tools", "subdir": "plugins/gemini-tools"},
    {"name": "headless", "desc": "Headless browser automation", "skills": 0, "repo": "paddo/claude-tools", "subdir": "plugins/headless"},
    # External plugins in official marketplace
    {"name": "discord", "desc": "Discord messaging bridge with access control", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/discord"},
    {"name": "slack", "desc": "Slack integration for Claude Code", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/slack"},
    {"name": "linear", "desc": "Linear issue tracking integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/linear"},
    {"name": "github", "desc": "GitHub integration for issues and PRs", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/github"},
    {"name": "gitlab", "desc": "GitLab integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/gitlab"},
    {"name": "firebase", "desc": "Firebase integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/firebase"},
    {"name": "terraform", "desc": "Terraform IaC automation and planning", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/terraform"},
    {"name": "telegram", "desc": "Telegram messaging integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/telegram"},
    {"name": "greptile", "desc": "Codebase search and understanding", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/greptile"},
    {"name": "asana", "desc": "Asana project management integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "external_plugins/asana"},
    # Additional internal plugins we missed
    {"name": "hookify", "desc": "Hook management and creation tools", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/hookify"},
    {"name": "plugin-dev", "desc": "Plugin development tools", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/plugin-dev"},
    {"name": "mcp-server-dev", "desc": "MCP server development tools", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/mcp-server-dev"},
    # Additional LSPs from official
    {"name": "kotlin-lsp", "desc": "Kotlin language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/kotlin-lsp"},
    {"name": "ruby-lsp", "desc": "Ruby language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/ruby-lsp"},
    {"name": "swift-lsp", "desc": "Swift language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/swift-lsp"},
    {"name": "php-lsp", "desc": "PHP language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/php-lsp"},
    {"name": "lua-lsp", "desc": "Lua language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/lua-lsp"},
    {"name": "csharp-lsp", "desc": "C# language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/csharp-lsp"},
    {"name": "jdtls-lsp", "desc": "Java language server integration", "skills": 0, "repo": _OFFICIAL_MARKETPLACE, "subdir": "plugins/jdtls-lsp"},
]


def get_all_known_plugins() -> list[dict]:
    """Return all known plugins (official + community) sorted by skill count.

    Official entries take precedence over community entries with the same name.
    """
    seen: set[str] = set()
    all_plugins: list[dict] = []
    for p in OFFICIAL_PLUGINS:
        seen.add(p["name"])
        all_plugins.append({**p, "source": "official"})
    for p in COMMUNITY_PLUGINS:
        if p["name"] not in seen:
            seen.add(p["name"])
            all_plugins.append({**p, "source": "community"})
    all_plugins.sort(key=lambda x: (-x["skills"], x["name"]))
    return all_plugins


async def search_clawhub_skills(query: str = "", limit: int = 30) -> list[tuple[str, str]]:
    """Search ClawHub.ai skill marketplace (44,000+ skills)."""
    import httpx

    # If no query, fetch popular categories to get a good mix
    queries = [query] if query else ["code", "test", "review", "debug", "deploy", "security", "api", "frontend"]

    results: list[tuple[str, str]] = []
    seen_slugs: set[str] = set()

    async with httpx.AsyncClient(timeout=8.0) as client:
        for q in queries:
            if len(results) >= limit:
                break
            try:
                resp = await client.get(
                    "https://clawhub.ai/api/search",
                    params={"q": q, "limit": min(10, limit - len(results))},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("results", []):
                    slug = item.get("slug", "")
                    if slug and slug not in seen_slugs:
                        seen_slugs.add(slug)
                        name = item.get("displayName") or slug
                        summary = item.get("summary", "")[:60]
                        results.append((slug, f"{name} — {summary}"))
            except Exception:
                continue

    return results[:limit]


async def search_clawhub_plugins(query: str, limit: int = 30) -> list[tuple[str, str]]:
    """Search ClawHub.ai plugin marketplace."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://clawhub.ai/api/search",
            params={"q": f"plugin {query}", "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("results", []):
        name = item.get("displayName") or item.get("slug", "")
        slug = item.get("slug", "")
        summary = item.get("summary", "")[:70]
        results.append((slug, f"{name} — {summary}"))
    return results

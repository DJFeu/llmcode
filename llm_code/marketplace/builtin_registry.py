"""Built-in registry of known Claude Code plugins (official + community)."""
from __future__ import annotations

OFFICIAL_PLUGINS = [
    {"name": "superpowers", "desc": "Core skills: TDD, debugging, collaboration, plans", "skills": 28, "repo": "obra/superpowers"},
    {"name": "data-engineering", "desc": "Data warehouse exploration, pipeline audit, SQL", "skills": 46, "repo": ""},
    {"name": "searchfit-seo", "desc": "AI-powered SEO toolkit — audit, content strategy", "skills": 22, "repo": ""},
    {"name": "chrome-devtools-mcp", "desc": "Browser automation, debugging, performance analysis", "skills": 8, "repo": "anthropics/claude-code-chrome-devtools-mcp"},
    {"name": "figma", "desc": "Figma MCP server + design-to-code skills", "skills": 7, "repo": ""},
    {"name": "frontend-design", "desc": "UI/UX implementation skill for web frontends", "skills": 3, "repo": ""},
    {"name": "skill-creator", "desc": "Create, improve, and measure skill performance", "skills": 3, "repo": ""},
    {"name": "remember", "desc": "Continuous memory — extract and persist context", "skills": 2, "repo": ""},
    {"name": "claude-code-setup", "desc": "Analyze codebases, recommend Claude Code automations", "skills": 1, "repo": ""},
    {"name": "claude-md-management", "desc": "Audit and improve CLAUDE.md files", "skills": 1, "repo": ""},
    {"name": "agent-sdk-dev", "desc": "Claude Agent SDK development tools", "skills": 0, "repo": ""},
    {"name": "code-review", "desc": "Automated code review with specialized agents", "skills": 0, "repo": ""},
    {"name": "code-simplifier", "desc": "Simplify and refine code for clarity", "skills": 0, "repo": ""},
    {"name": "commit-commands", "desc": "Streamline git workflow — commit, push, PR", "skills": 0, "repo": ""},
    {"name": "context7", "desc": "Context7 MCP for up-to-date documentation lookup", "skills": 0, "repo": ""},
    {"name": "explanatory-output-style", "desc": "Educational insights about implementation choices", "skills": 0, "repo": ""},
    {"name": "feature-dev", "desc": "Feature development workflow with specialized agents", "skills": 0, "repo": ""},
    {"name": "playwright", "desc": "Browser automation and E2E testing (Microsoft)", "skills": 0, "repo": ""},
    {"name": "pr-review-toolkit", "desc": "Comprehensive PR review with specialized agents", "skills": 0, "repo": ""},
    {"name": "ralph-loop", "desc": "Continuous self-referential AI loops", "skills": 0, "repo": ""},
    {"name": "security-guidance", "desc": "Security reminder hooks for safe coding", "skills": 0, "repo": ""},
    {"name": "semgrep", "desc": "Semgrep MCP for static analysis and SAST", "skills": 0, "repo": ""},
    {"name": "supabase", "desc": "Supabase MCP for database and auth operations", "skills": 0, "repo": ""},
    {"name": "clangd-lsp", "desc": "C/C++ language server integration", "skills": 0, "repo": ""},
    {"name": "gopls-lsp", "desc": "Go language server integration", "skills": 0, "repo": ""},
    {"name": "pyright-lsp", "desc": "Python language server integration", "skills": 0, "repo": ""},
    {"name": "rust-analyzer-lsp", "desc": "Rust language server integration", "skills": 0, "repo": ""},
    {"name": "typescript-lsp", "desc": "TypeScript language server integration", "skills": 0, "repo": ""},
]

COMMUNITY_PLUGINS = [
    {"name": "ai-integration-architect", "desc": "Design and scaffold AI integration into enterprise systems", "skills": 1, "repo": ""},
    {"name": "claude-md-optimizer", "desc": "Optimize oversized CLAUDE.md using progressive disclosure", "skills": 1, "repo": ""},
]


def get_all_known_plugins() -> list[dict]:
    """Return all known plugins (official + community) sorted by skill count."""
    all_plugins = []
    for p in OFFICIAL_PLUGINS:
        all_plugins.append({**p, "source": "official"})
    for p in COMMUNITY_PLUGINS:
        all_plugins.append({**p, "source": "community"})
    all_plugins.sort(key=lambda x: (-x["skills"], x["name"]))
    return all_plugins

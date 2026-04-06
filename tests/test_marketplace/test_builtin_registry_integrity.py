"""Tests for builtin registry integrity — prevent the bugs found during testing.

Covers:
  3. Duplicate entries in marketplace (official vs community)
  4. "No repo URL" message for all plugins (wrong fallback)
  7. Registry pointing to wrong repos
"""
from __future__ import annotations

from llm_code.marketplace.builtin_registry import (
    COMMUNITY_PLUGINS,
    OFFICIAL_PLUGINS,
    get_all_known_plugins,
)


# ---------------------------------------------------------------------------
# TestNoDuplicates — Bug #3
# ---------------------------------------------------------------------------


class TestNoDuplicates:
    """No duplicate plugin names within or across the two lists."""

    def test_no_duplicate_names_in_official(self) -> None:
        names = [p["name"] for p in OFFICIAL_PLUGINS]
        dupes = [n for n in names if names.count(n) > 1]
        assert len(names) == len(set(names)), f"Duplicate official names: {dupes}"

    def test_no_duplicate_names_in_community(self) -> None:
        names = [p["name"] for p in COMMUNITY_PLUGINS]
        dupes = [n for n in names if names.count(n) > 1]
        assert len(names) == len(set(names)), f"Duplicate community names: {dupes}"

    def test_no_overlap_official_community(self) -> None:
        """Official and community must not share a plugin name."""
        official_names = {p["name"] for p in OFFICIAL_PLUGINS}
        community_names = {p["name"] for p in COMMUNITY_PLUGINS}
        overlap = official_names & community_names
        assert not overlap, f"Name overlap between official and community: {overlap}"

    def test_get_all_deduplicates(self) -> None:
        """get_all_known_plugins should return each name exactly once."""
        all_plugins = get_all_known_plugins()
        names = [p["name"] for p in all_plugins]
        assert len(names) == len(set(names)), (
            f"get_all_known_plugins has duplicates: "
            f"{[n for n in names if names.count(n) > 1]}"
        )


# ---------------------------------------------------------------------------
# TestRegistryFields — Bug #4
# ---------------------------------------------------------------------------


class TestRegistryFields:
    """Every entry must have the fields the UI depends on."""

    def test_all_have_required_fields(self) -> None:
        """Every plugin must have name, desc, skills, repo."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            assert "name" in p, f"Missing 'name': {p}"
            assert "desc" in p, f"Missing 'desc' in {p['name']}"
            assert "skills" in p, f"Missing 'skills' in {p['name']}"
            assert "repo" in p, f"Missing 'repo' in {p['name']}"

    def test_all_repos_are_nonempty(self) -> None:
        """Bug #4: repo field must never be empty/falsy."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            assert p["repo"], f"Plugin {p['name']} has empty/falsy repo"

    def test_official_plugins_have_repo(self) -> None:
        """Official plugins must have a valid repo (standalone or marketplace subdir)."""
        for p in OFFICIAL_PLUGINS:
            assert p["repo"], f"Official plugin {p['name']} has no repo"

    def test_names_are_kebab_case(self) -> None:
        """Plugin names should be lowercase kebab-case (no spaces, underscores)."""
        import re

        pattern = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            assert pattern.match(p["name"]), (
                f"Plugin name is not kebab-case: {p['name']}"
            )

    def test_skills_count_is_nonnegative_int(self) -> None:
        """Skills count must be a non-negative integer."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            assert isinstance(p["skills"], int), (
                f"skills should be int for {p['name']}, got {type(p['skills'])}"
            )
            assert p["skills"] >= 0, f"Negative skills count for {p['name']}"

    def test_desc_is_nonempty_string(self) -> None:
        """Description must be a non-empty string."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            assert isinstance(p["desc"], str) and p["desc"].strip(), (
                f"Empty or non-string desc for {p['name']}"
            )


# ---------------------------------------------------------------------------
# TestSubdirFormat — Bug #6, #7
# ---------------------------------------------------------------------------


class TestSubdirFormat:
    """Subdir paths must be relative, valid, and point to correct locations."""

    def test_subdir_is_relative(self) -> None:
        """subdir must not start with / (it is relative to the repo root)."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            subdir = p.get("subdir", "")
            if subdir:
                assert not subdir.startswith("/"), (
                    f"subdir should be relative for {p['name']}: {subdir}"
                )

    def test_subdir_has_path_separator(self) -> None:
        """subdir should be a proper path like 'plugins/name' or 'external_plugins/name'."""
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            subdir = p.get("subdir", "")
            if subdir:
                assert "/" in subdir, (
                    f"subdir should be path-like for {p['name']}: {subdir}"
                )

    def test_subdir_no_trailing_slash(self) -> None:
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            subdir = p.get("subdir", "")
            if subdir:
                assert not subdir.endswith("/"), (
                    f"subdir should not end with / for {p['name']}: {subdir}"
                )

    def test_official_marketplace_subdirs_use_correct_prefixes(self) -> None:
        """Bug #7: Plugins in the official marketplace must use either
        'plugins/' or 'external_plugins/' prefix."""
        marketplace = "anthropics/claude-plugins-official"
        valid_prefixes = ("plugins/", "external_plugins/")
        for p in OFFICIAL_PLUGINS + COMMUNITY_PLUGINS:
            if p["repo"] == marketplace:
                subdir = p.get("subdir", "")
                assert subdir, (
                    f"Marketplace plugin {p['name']} needs a subdir"
                )
                assert subdir.startswith(valid_prefixes), (
                    f"Plugin {p['name']} subdir '{subdir}' does not start with "
                    f"plugins/ or external_plugins/"
                )


# ---------------------------------------------------------------------------
# TestOfficialPrecedence — get_all_known_plugins ordering
# ---------------------------------------------------------------------------


class TestOfficialPrecedence:
    """Official plugins must take precedence when names overlap."""

    def test_official_source_tag(self) -> None:
        """All OFFICIAL_PLUGINS should get source='official' in merged list."""
        all_plugins = get_all_known_plugins()
        official_names = {p["name"] for p in OFFICIAL_PLUGINS}
        for p in all_plugins:
            if p["name"] in official_names:
                assert p["source"] == "official", (
                    f"{p['name']} should be source='official'"
                )

    def test_community_source_tag(self) -> None:
        """Plugins only in COMMUNITY_PLUGINS get source='community'."""
        all_plugins = get_all_known_plugins()
        official_names = {p["name"] for p in OFFICIAL_PLUGINS}
        for p in all_plugins:
            if p["name"] not in official_names:
                assert p["source"] == "community", (
                    f"{p['name']} should be source='community'"
                )

    def test_all_plugins_have_source_field(self) -> None:
        """Every entry from get_all_known_plugins must have a source field."""
        all_plugins = get_all_known_plugins()
        for p in all_plugins:
            assert "source" in p, f"Missing 'source' field for {p['name']}"
            assert p["source"] in ("official", "community"), (
                f"Invalid source '{p['source']}' for {p['name']}"
            )

    def test_total_count_equals_deduplicated_union(self) -> None:
        """Total count must match the union of both lists (deduped by name)."""
        official_names = {p["name"] for p in OFFICIAL_PLUGINS}
        community_only = {p["name"] for p in COMMUNITY_PLUGINS} - official_names
        expected = len(official_names) + len(community_only)

        all_plugins = get_all_known_plugins()
        assert len(all_plugins) == expected

    def test_sorted_by_skills_desc_then_name(self) -> None:
        """get_all_known_plugins returns results sorted by -skills, then name."""
        all_plugins = get_all_known_plugins()
        for i in range(len(all_plugins) - 1):
            a, b = all_plugins[i], all_plugins[i + 1]
            if a["skills"] == b["skills"]:
                assert a["name"] <= b["name"], (
                    f"Sort violated: {a['name']} should come before {b['name']}"
                )
            else:
                assert a["skills"] >= b["skills"], (
                    f"Sort violated: {a['name']} ({a['skills']}) should come "
                    f"before {b['name']} ({b['skills']})"
                )

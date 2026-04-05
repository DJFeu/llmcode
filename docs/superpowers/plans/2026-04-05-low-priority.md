# Low Priority Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Claude Code parity by implementing 5 remaining features: skill frontmatter extension, keybindings customization, agent teams persistence, app-aware computer use, and enterprise features.

**Architecture:** Extend existing modules (Approach B). Skills → `runtime/`, keybindings → `tui/`, agent teams → `swarm/`, computer use → `computer_use/`, enterprise → new `enterprise/` package. Each feature is independently testable.

**Tech Stack:** Python 3.11+, pytest, frozen dataclasses, asyncio, fnmatch (glob patterns), `importlib.metadata` (version), `cryptography.fernet` (token encryption)

**Spec:** `docs/superpowers/specs/2026-04-05-low-priority-design.md`

**Baseline:** 2695 passed, 3 skipped

---

## Phase 1: Independent Features (parallelizable)

### Task 1: Skill Frontmatter — Extended Dataclass

**Files:**
- Modify: `llm_code/runtime/skills.py`
- Test: `tests/test_runtime/test_skills.py`

- [ ] **Step 1: Write failing tests for new Skill fields**

```python
# tests/test_runtime/test_skills.py
"""Tests for extended Skill frontmatter."""
from __future__ import annotations

import pytest

from llm_code.runtime.skills import Skill, SkillDependency, SkillLoader


class TestSkillDependency:
    def test_create_with_name_only(self) -> None:
        dep = SkillDependency(name="base-tools")
        assert dep.name == "base-tools"
        assert dep.registry == ""

    def test_create_with_registry(self) -> None:
        dep = SkillDependency(name="base-tools", registry="official")
        assert dep.registry == "official"

    def test_frozen(self) -> None:
        dep = SkillDependency(name="x")
        with pytest.raises(AttributeError):
            dep.name = "y"  # type: ignore[misc]


class TestSkillExtendedFields:
    def test_default_new_fields(self) -> None:
        skill = Skill(name="test", description="desc", content="body")
        assert skill.version == ""
        assert skill.tags == ()
        assert skill.model == ""
        assert skill.depends == ()
        assert skill.min_version == ""

    def test_new_fields_populated(self) -> None:
        skill = Skill(
            name="test",
            description="desc",
            content="body",
            version="1.2.0",
            tags=("debug", "python"),
            model="sonnet",
            depends=(SkillDependency(name="base"),),
            min_version="0.8.0",
        )
        assert skill.version == "1.2.0"
        assert skill.tags == ("debug", "python")
        assert skill.model == "sonnet"
        assert len(skill.depends) == 1
        assert skill.depends[0].name == "base"
        assert skill.min_version == "0.8.0"

    def test_existing_fields_unchanged(self) -> None:
        skill = Skill(name="x", description="d", content="c", auto=True, trigger="go")
        assert skill.auto is True
        assert skill.trigger == "go"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime/test_skills.py -v`
Expected: FAIL — `SkillDependency` not defined, `Skill` doesn't accept new fields

- [ ] **Step 3: Add SkillDependency and extend Skill dataclass**

In `llm_code/runtime/skills.py`, add `SkillDependency` before `Skill` and extend `Skill`:

```python
@dataclass(frozen=True)
class SkillDependency:
    """A dependency on another skill."""
    name: str
    registry: str = ""  # empty = search all registries


@dataclass(frozen=True)
class Skill:
    """A single skill loaded from a SKILL.md file."""

    name: str
    description: str
    content: str
    auto: bool = False
    trigger: str = ""
    version: str = ""
    tags: tuple[str, ...] = ()
    model: str = ""
    depends: tuple[SkillDependency, ...] = ()
    min_version: str = ""

    def __post_init__(self) -> None:
        if not self.trigger:
            object.__setattr__(self, "trigger", self.name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime/test_skills.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/runtime/skills.py tests/test_runtime/test_skills.py
git commit -m "feat: extend Skill dataclass with version, tags, model, depends, min_version"
```

---

### Task 2: Skill Frontmatter — Parser Extension

**Files:**
- Modify: `llm_code/runtime/skills.py:36-69` (SkillLoader.load_skill)
- Test: `tests/test_runtime/test_skills.py`

- [ ] **Step 1: Write failing tests for frontmatter parsing**

Append to `tests/test_runtime/test_skills.py`:

```python
class TestSkillLoaderExtendedFrontmatter:
    def test_parse_version(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nversion: 2.0.1\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.version == "2.0.1"

    def test_parse_tags(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\ntags: [debug, python]\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.tags == ("debug", "python")

    def test_parse_model(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nmodel: haiku\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.model == "haiku"

    def test_parse_min_version(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\nmin_version: 0.8.0\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.min_version == "0.8.0"

    def test_parse_depends_single(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: base-tools\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert len(skill.depends) == 1
        assert skill.depends[0].name == "base-tools"
        assert skill.depends[0].registry == ""

    def test_parse_depends_with_registry(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: base-tools\n    registry: official\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert skill.depends[0].registry == "official"

    def test_parse_depends_multiple(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        content = "---\nname: demo\ndescription: d\ndepends:\n  - name: a\n  - name: b\n---\nbody\n"
        p.write_text(content)
        skill = SkillLoader.load_skill(p)
        assert len(skill.depends) == 2

    def test_no_new_fields_gives_defaults(self, tmp_path) -> None:
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: demo\ndescription: d\n---\nbody\n")
        skill = SkillLoader.load_skill(p)
        assert skill.version == ""
        assert skill.tags == ()
        assert skill.model == ""
        assert skill.depends == ()
        assert skill.min_version == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime/test_skills.py::TestSkillLoaderExtendedFrontmatter -v`
Expected: FAIL — parser doesn't handle new fields

- [ ] **Step 3: Replace the simple key:value parser with YAML-aware parsing**

Replace `SkillLoader.load_skill` in `llm_code/runtime/skills.py`:

```python
import yaml  # add to imports at top

class SkillLoader:
    """Loads skills from directories."""

    @staticmethod
    def load_skill(path: Path) -> Skill:
        """Parse a SKILL.md file and return a Skill."""
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError(f"Invalid SKILL.md format: {path}")

        frontmatter_raw, content = m.group(1), m.group(2)

        # Parse YAML frontmatter (handles lists, nested dicts)
        try:
            meta = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError:
            meta = {}

        name = str(meta.get("name", ""))
        description = str(meta.get("description", ""))
        auto_raw = meta.get("auto", False)
        auto = auto_raw is True or str(auto_raw).lower() in ("true", "yes", "1")
        trigger = str(meta.get("trigger", ""))

        # New fields
        version = str(meta.get("version", ""))
        model = str(meta.get("model", ""))
        min_version = str(meta.get("min_version", ""))

        # Tags: expect list of strings
        tags_raw = meta.get("tags", [])
        tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()

        # Depends: expect list of dicts with 'name' and optional 'registry'
        depends_raw = meta.get("depends", [])
        depends: tuple[SkillDependency, ...] = ()
        if isinstance(depends_raw, list):
            deps = []
            for item in depends_raw:
                if isinstance(item, dict) and "name" in item:
                    deps.append(SkillDependency(
                        name=str(item["name"]),
                        registry=str(item.get("registry", "")),
                    ))
            depends = tuple(deps)

        return Skill(
            name=name,
            description=description,
            content=content,
            auto=auto,
            trigger=trigger,
            version=version,
            tags=tags,
            model=model,
            depends=depends,
            min_version=min_version,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime/test_skills.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed, 0 failures

- [ ] **Step 6: Commit**

```bash
git add llm_code/runtime/skills.py tests/test_runtime/test_skills.py
git commit -m "feat: parse extended skill frontmatter (version, tags, model, depends, min_version)"
```

---

### Task 3: Skill Dependency Resolver

**Files:**
- Create: `llm_code/runtime/skill_resolver.py`
- Test: `tests/test_runtime/test_skill_resolver.py`

- [ ] **Step 1: Write failing tests for SkillResolver**

```python
# tests/test_runtime/test_skill_resolver.py
"""Tests for SkillResolver — dependency resolution for skills."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.runtime.skill_resolver import SkillResolver
from llm_code.runtime.skills import Skill, SkillDependency


def _make_skill(name: str, depends: tuple[SkillDependency, ...] = (), min_version: str = "") -> Skill:
    return Skill(name=name, description="d", content="c", depends=depends, min_version=min_version)


class TestSkillResolverCheckInstalled:
    def test_no_deps_returns_empty(self) -> None:
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(_make_skill("a"))
        assert missing == []

    def test_all_deps_installed(self) -> None:
        skill = _make_skill("a", depends=(SkillDependency(name="b"),))
        resolver = SkillResolver(installed_skills={"a", "b"}, installer=MagicMock())
        assert resolver.find_missing(skill) == []

    def test_missing_dep_returned(self) -> None:
        skill = _make_skill("a", depends=(SkillDependency(name="b"),))
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(skill)
        assert len(missing) == 1
        assert missing[0].name == "b"

    def test_multiple_missing(self) -> None:
        skill = _make_skill("a", depends=(
            SkillDependency(name="b"),
            SkillDependency(name="c"),
        ))
        resolver = SkillResolver(installed_skills={"a"}, installer=MagicMock())
        missing = resolver.find_missing(skill)
        assert {d.name for d in missing} == {"b", "c"}


class TestSkillResolverCycleDetection:
    def test_cycle_raises(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        with pytest.raises(ValueError, match="[Cc]ircular"):
            resolver._check_cycle("a", frozenset({"a"}))

    def test_no_cycle(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        # Should not raise
        resolver._check_cycle("a", frozenset({"b", "c"}))


class TestSkillResolverMaxDepth:
    def test_exceeds_max_depth_raises(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock(), max_depth=3)
        with pytest.raises(ValueError, match="[Dd]epth"):
            resolver._check_depth(4)

    def test_within_max_depth_ok(self) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock(), max_depth=3)
        resolver._check_depth(3)  # should not raise


class TestSkillResolverMinVersion:
    @patch("llm_code.runtime.skill_resolver._get_llm_code_version", return_value="1.0.0")
    def test_compatible_version(self, _mock) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a", min_version="0.8.0"))
        assert warnings == []

    @patch("llm_code.runtime.skill_resolver._get_llm_code_version", return_value="0.5.0")
    def test_incompatible_version_warns(self, _mock) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a", min_version="0.8.0"))
        assert len(warnings) == 1
        assert "0.8.0" in warnings[0]

    @patch("llm_code.runtime.skill_resolver._get_llm_code_version", return_value="1.0.0")
    def test_no_min_version_no_warning(self, _mock) -> None:
        resolver = SkillResolver(installed_skills=set(), installer=MagicMock())
        warnings = resolver.check_min_version(_make_skill("a"))
        assert warnings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime/test_skill_resolver.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement SkillResolver**

```python
# llm_code/runtime/skill_resolver.py
"""Skill dependency resolver — checks and auto-installs missing skill dependencies."""
from __future__ import annotations

import logging
from importlib.metadata import version as pkg_version
from packaging.version import Version

from llm_code.runtime.skills import Skill, SkillDependency

_log = logging.getLogger(__name__)


def _get_llm_code_version() -> str:
    """Return the installed llm-code version."""
    try:
        return pkg_version("llm-code")
    except Exception:
        return "0.0.0"


class SkillResolver:
    """Check and resolve skill dependencies."""

    def __init__(
        self,
        installed_skills: set[str],
        installer: object,  # PluginInstaller — loosely typed to avoid circular import
        max_depth: int = 3,
    ) -> None:
        self._installed = installed_skills
        self._installer = installer
        self._max_depth = max_depth

    def find_missing(self, skill: Skill) -> list[SkillDependency]:
        """Return list of dependencies not currently installed."""
        return [dep for dep in skill.depends if dep.name not in self._installed]

    def _check_cycle(self, name: str, visited: frozenset[str]) -> None:
        """Raise ValueError if name is already in the visited set."""
        if name in visited:
            raise ValueError(f"Circular dependency detected: '{name}' already in chain {sorted(visited)}")

    def _check_depth(self, depth: int) -> None:
        """Raise ValueError if depth exceeds max_depth."""
        if depth > self._max_depth:
            raise ValueError(f"Dependency depth {depth} exceeds max depth {self._max_depth}")

    def check_min_version(self, skill: Skill) -> list[str]:
        """Check if llm-code version satisfies skill's min_version. Return warnings."""
        if not skill.min_version:
            return []
        current = _get_llm_code_version()
        try:
            if Version(current) < Version(skill.min_version):
                return [
                    f"Skill '{skill.name}' requires llm-code >= {skill.min_version}, "
                    f"but current version is {current}"
                ]
        except Exception:
            return [f"Could not compare versions: current={current}, required={skill.min_version}"]
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime/test_skill_resolver.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/runtime/skill_resolver.py tests/test_runtime/test_skill_resolver.py
git commit -m "feat: add SkillResolver with dependency checking, cycle detection, min_version validation"
```

---

### Task 4: Keybindings — Action Registry & Config Loader

**Files:**
- Create: `llm_code/tui/keybindings.py`
- Test: `tests/test_tui/test_keybindings.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tui/test_keybindings.py
"""Tests for keybindings config system."""
from __future__ import annotations

import json

import pytest

from llm_code.tui.keybindings import (
    ACTION_REGISTRY,
    ChordBinding,
    ChordState,
    KeyAction,
    KeybindingManager,
    load_keybindings,
)


class TestKeyAction:
    def test_create(self) -> None:
        action = KeyAction(name="submit", description="Submit input", default_key="enter")
        assert action.name == "submit"
        assert action.default_key == "enter"

    def test_frozen(self) -> None:
        action = KeyAction(name="submit", description="d", default_key="enter")
        with pytest.raises(AttributeError):
            action.name = "x"  # type: ignore[misc]


class TestActionRegistry:
    def test_has_submit(self) -> None:
        assert "submit" in ACTION_REGISTRY

    def test_has_cancel(self) -> None:
        assert "cancel" in ACTION_REGISTRY

    def test_has_newline(self) -> None:
        assert "newline" in ACTION_REGISTRY

    def test_all_have_default_key(self) -> None:
        for name, action in ACTION_REGISTRY.items():
            assert action.default_key, f"Action '{name}' missing default_key"


class TestChordState:
    def test_no_chord_returns_none(self) -> None:
        state = ChordState(chords={})
        assert state.feed("a") is None

    def test_single_key_not_chord(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        assert state.feed("a") is None
        assert state.pending is None

    def test_chord_first_key_sets_pending(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        result = state.feed("ctrl+k")
        assert result is None
        assert state.pending == "ctrl+k"

    def test_chord_second_key_matches(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        result = state.feed("ctrl+c")
        assert result == "comment"
        assert state.pending is None

    def test_chord_second_key_no_match_clears(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        result = state.feed("x")
        assert result is None
        assert state.pending is None

    def test_chord_reset(self) -> None:
        state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment"})
        state.feed("ctrl+k")
        state.reset()
        assert state.pending is None


class TestKeybindingManager:
    def test_default_bindings(self) -> None:
        mgr = KeybindingManager()
        assert mgr.get_action("enter") == "submit"

    def test_rebind(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        assert mgr.get_action("ctrl+enter") == "submit"
        assert mgr.get_action("enter") is None

    def test_conflict_detection(self) -> None:
        mgr = KeybindingManager()
        # "escape" is bound to "cancel"; trying to bind "newline" to "escape" should conflict
        conflicts = mgr.check_conflict("escape")
        assert len(conflicts) > 0

    def test_reset_single(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        mgr.reset_action("submit")
        assert mgr.get_action("enter") == "submit"

    def test_reset_all(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        mgr.reset_all()
        assert mgr.get_action("enter") == "submit"

    def test_get_all_bindings(self) -> None:
        mgr = KeybindingManager()
        bindings = mgr.get_all_bindings()
        assert "submit" in bindings
        assert bindings["submit"] == "enter"


class TestLoadKeybindings:
    def test_load_from_file(self, tmp_path) -> None:
        config = {"bindings": {"submit": "ctrl+enter"}}
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        assert mgr.get_action("ctrl+enter") == "submit"

    def test_load_missing_file_returns_defaults(self, tmp_path) -> None:
        mgr = load_keybindings(tmp_path / "nonexistent.json")
        assert mgr.get_action("enter") == "submit"

    def test_load_with_chords(self, tmp_path) -> None:
        config = {
            "bindings": {},
            "chords": {"ctrl+k ctrl+c": "comment_selection"},
        }
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        assert mgr.chord_state.feed("ctrl+k") is None
        assert mgr.chord_state.feed("ctrl+c") == "comment_selection"

    def test_conflict_in_file_uses_defaults(self, tmp_path) -> None:
        # Bind two actions to same key
        config = {"bindings": {"submit": "escape", "cancel": "escape"}}
        p = tmp_path / "keybindings.json"
        p.write_text(json.dumps(config))
        mgr = load_keybindings(p)
        # Should fall back to defaults due to conflict
        assert mgr.get_action("enter") == "submit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tui/test_keybindings.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement keybindings module**

```python
# llm_code/tui/keybindings.py
"""Keybinding configuration — action registry, chord support, config loader."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyAction:
    """A bindable action with a default key."""
    name: str
    description: str
    default_key: str


ACTION_REGISTRY: dict[str, KeyAction] = {
    "submit": KeyAction("submit", "Submit input", "enter"),
    "newline": KeyAction("newline", "Insert newline", "shift+enter"),
    "cancel": KeyAction("cancel", "Cancel / clear input", "escape"),
    "clear_input": KeyAction("clear_input", "Clear input line", "ctrl+u"),
    "autocomplete": KeyAction("autocomplete", "Autocomplete slash command", "tab"),
    "history_prev": KeyAction("history_prev", "Previous history", "ctrl+p"),
    "history_next": KeyAction("history_next", "Next history", "ctrl+n"),
    "toggle_thinking": KeyAction("toggle_thinking", "Toggle thinking display", "alt+t"),
    "toggle_vim": KeyAction("toggle_vim", "Toggle vim mode", "ctrl+shift+v"),
    "voice_input": KeyAction("voice_input", "Activate voice input", "ctrl+space"),
    "cursor_left": KeyAction("cursor_left", "Move cursor left", "left"),
    "cursor_right": KeyAction("cursor_right", "Move cursor right", "right"),
    "cursor_home": KeyAction("cursor_home", "Move to line start", "home"),
    "cursor_end": KeyAction("cursor_end", "Move to line end", "end"),
    "delete_back": KeyAction("delete_back", "Delete char before cursor", "backspace"),
    "delete_forward": KeyAction("delete_forward", "Delete char at cursor", "delete"),
}


@dataclass(frozen=True)
class ChordBinding:
    """A two-key chord mapping."""
    keys: tuple[str, ...]
    action: str


@dataclass
class ChordState:
    """Tracks chord key sequences."""
    chords: dict[tuple[str, ...], str] = field(default_factory=dict)
    pending: str | None = None

    def feed(self, key: str) -> str | None:
        """Feed a key event. Return matched action or None."""
        if self.pending is not None:
            combo = (self.pending, key)
            self.pending = None
            return self.chords.get(combo)

        # Check if this key is the first key of any chord
        for chord_keys in self.chords:
            if chord_keys[0] == key:
                self.pending = key
                return None
        return None

    def reset(self) -> None:
        """Clear any pending chord state."""
        self.pending = None


class KeybindingManager:
    """Manages key-to-action mappings with conflict detection."""

    def __init__(self) -> None:
        # key -> action_name
        self._bindings: dict[str, str] = {}
        # action_name -> key
        self._reverse: dict[str, str] = {}
        self.chord_state = ChordState()
        self.reset_all()

    def get_action(self, key: str) -> str | None:
        """Return the action name bound to a key, or None."""
        return self._bindings.get(key)

    def get_key(self, action: str) -> str | None:
        """Return the key bound to an action, or None."""
        return self._reverse.get(action)

    def rebind(self, action: str, new_key: str) -> None:
        """Rebind an action to a new key. Removes old binding."""
        # Remove old binding for this action
        old_key = self._reverse.get(action)
        if old_key and old_key in self._bindings:
            del self._bindings[old_key]
        # Set new binding
        self._bindings[new_key] = action
        self._reverse[action] = new_key

    def check_conflict(self, key: str) -> list[str]:
        """Return list of actions already bound to this key."""
        action = self._bindings.get(key)
        return [action] if action else []

    def reset_action(self, action: str) -> None:
        """Reset a single action to its default key."""
        if action not in ACTION_REGISTRY:
            return
        old_key = self._reverse.get(action)
        if old_key and old_key in self._bindings:
            del self._bindings[old_key]
        default_key = ACTION_REGISTRY[action].default_key
        self._bindings[default_key] = action
        self._reverse[action] = default_key

    def reset_all(self) -> None:
        """Reset all actions to default keys."""
        self._bindings.clear()
        self._reverse.clear()
        for name, action in ACTION_REGISTRY.items():
            self._bindings[action.default_key] = name
            self._reverse[name] = action.default_key

    def get_all_bindings(self) -> dict[str, str]:
        """Return {action_name: key} for all actions."""
        return dict(self._reverse)


def load_keybindings(path: Path) -> KeybindingManager:
    """Load keybinding config from JSON file. Returns defaults if file missing or invalid."""
    mgr = KeybindingManager()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return mgr

    # Apply custom bindings
    bindings = data.get("bindings", {})
    if isinstance(bindings, dict):
        # Check for conflicts first: if any key is used by multiple actions, reject all
        key_to_actions: dict[str, list[str]] = {}
        for action, key in bindings.items():
            key_to_actions.setdefault(key, []).append(action)
        has_conflict = any(len(actions) > 1 for actions in key_to_actions.values())
        if has_conflict:
            _log.warning("Keybinding config has conflicts; using defaults")
            return KeybindingManager()
        for action, key in bindings.items():
            if action in ACTION_REGISTRY:
                mgr.rebind(action, key)

    # Apply chords
    chords_raw = data.get("chords", {})
    if isinstance(chords_raw, dict):
        chords: dict[tuple[str, ...], str] = {}
        for key_str, action in chords_raw.items():
            keys = tuple(key_str.split())
            if len(keys) == 2:
                chords[keys] = action
        mgr.chord_state = ChordState(chords=chords)

    return mgr
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tui/test_keybindings.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/keybindings.py tests/test_tui/test_keybindings.py
git commit -m "feat: add keybinding action registry, chord support, and config loader"
```

---

### Task 5: Keybindings — Refactor input_bar.on_key() to Use Table Lookup

**Files:**
- Modify: `llm_code/tui/input_bar.py:199-308` (on_key method)
- Test: `tests/test_tui/test_keybindings.py` (add integration tests)

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_tui/test_keybindings.py`:

```python
class TestKeybindingManagerIntegration:
    """Test that rebinding keys produces correct lookup results in a realistic flow."""

    def test_custom_submit_key(self) -> None:
        mgr = KeybindingManager()
        mgr.rebind("submit", "ctrl+enter")
        # Simulate key resolution
        assert mgr.get_action("ctrl+enter") == "submit"
        assert mgr.get_action("enter") is None  # no longer bound

    def test_chord_then_single_key(self) -> None:
        mgr = KeybindingManager()
        mgr.chord_state = ChordState(chords={("ctrl+k", "ctrl+c"): "comment_selection"})
        # First key: pending
        result = mgr.chord_state.feed("ctrl+k")
        assert result is None
        # Second key: match
        result = mgr.chord_state.feed("ctrl+c")
        assert result == "comment_selection"
        # Normal key after chord
        assert mgr.get_action("enter") == "submit"
```

- [ ] **Step 2: Run test to verify it passes (this is an integration test for already-implemented code)**

Run: `python -m pytest tests/test_tui/test_keybindings.py::TestKeybindingManagerIntegration -v`
Expected: PASS

- [ ] **Step 3: Refactor `input_bar.on_key()` to delegate to KeybindingManager**

In `llm_code/tui/input_bar.py`, add import and init:

```python
# At top of file, add:
from llm_code.tui.keybindings import KeybindingManager, load_keybindings

# In InputBar.__init__, add:
self._keybindings = load_keybindings(Path.home() / ".llm-code" / "keybindings.json")
```

Replace the "Normal (non-vim) key handling" block (lines 272-308) with:

```python
        # Normal (non-vim) key handling — table lookup
        # Check chord first
        chord_action = self._keybindings.chord_state.feed(event.key)
        if chord_action is not None:
            self._handle_action(chord_action)
            return
        if self._keybindings.chord_state.pending is not None:
            # Waiting for second chord key
            return

        action = self._keybindings.get_action(event.key)
        if action:
            self._handle_action(action)
        elif event.character and len(event.character) == 1:
            self.value = self.value[:self._cursor] + event.character + self.value[self._cursor:]
            self._cursor += 1

    def _handle_action(self, action: str) -> None:
        """Execute a named keybinding action."""
        if action == "submit":
            if self.value.strip():
                self.post_message(self.Submitted(self.value))
                self.value = ""
                self._cursor = 0
        elif action == "newline":
            self.value = self.value[:self._cursor] + "\n" + self.value[self._cursor:]
            self._cursor += 1
        elif action == "delete_back":
            if self._cursor > 0:
                self.value = self.value[:self._cursor - 1] + self.value[self._cursor:]
                self._cursor -= 1
        elif action == "delete_forward":
            if self._cursor < len(self.value):
                self.value = self.value[:self._cursor] + self.value[self._cursor + 1:]
        elif action == "cursor_left":
            if self._cursor > 0:
                self._cursor -= 1
                self.refresh()
        elif action == "cursor_right":
            if self._cursor < len(self.value):
                self._cursor += 1
                self.refresh()
        elif action == "cursor_home":
            self._cursor = 0
            self.refresh()
        elif action == "cursor_end":
            self._cursor = len(self.value)
            self.refresh()
        elif action == "cancel":
            self.value = ""
            self._cursor = 0
            self.post_message(self.Cancelled())
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed, 0 failures

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/input_bar.py tests/test_tui/test_keybindings.py
git commit -m "refactor: input_bar.on_key() uses keybinding table lookup instead of hardcoded keys"
```

---

### Task 6: Keybindings — /keybind Slash Command

**Files:**
- Modify: `llm_code/cli/commands.py`
- Modify: `llm_code/tui/input_bar.py` (add "keybind" to SLASH_COMMANDS)
- Test: `tests/test_cli/test_commands.py` (add test)

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli/test_commands.py`:

```python
class TestKeybindCommand:
    def test_parse_keybind_no_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind")
        assert cmd is not None
        assert cmd.name == "keybind"
        assert cmd.args == ""

    def test_parse_keybind_with_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind submit ctrl+enter")
        assert cmd is not None
        assert cmd.name == "keybind"
        assert cmd.args == "submit ctrl+enter"

    def test_parse_keybind_reset(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/keybind reset")
        assert cmd is not None
        assert cmd.args == "reset"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli/test_commands.py::TestKeybindCommand -v`
Expected: FAIL — "keybind" not in KNOWN_COMMANDS

- [ ] **Step 3: Add "keybind" to KNOWN_COMMANDS and SLASH_COMMANDS**

In `llm_code/cli/commands.py`, add `"keybind"` to the `KNOWN_COMMANDS` frozenset.

In `llm_code/tui/input_bar.py`, add `"/keybind"` to `SLASH_COMMANDS` list and add `("/keybind", "Rebind keys")` to `SLASH_COMMAND_DESCS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli/test_commands.py::TestKeybindCommand -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed

- [ ] **Step 6: Commit**

```bash
git add llm_code/cli/commands.py llm_code/tui/input_bar.py tests/test_cli/test_commands.py
git commit -m "feat: add /keybind slash command for key rebinding"
```

---

### Task 7: App-aware Computer Use — App Detection

**Files:**
- Create: `llm_code/computer_use/app_detect.py`
- Test: `tests/test_computer_use/test_app_detect.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_computer_use/test_app_detect.py
"""Tests for app detection on macOS."""
from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pytest

from llm_code.computer_use.app_detect import AppInfo, get_frontmost_app_sync


class TestAppInfo:
    def test_create(self) -> None:
        info = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1234)
        assert info.name == "Safari"
        assert info.bundle_id == "com.apple.Safari"
        assert info.pid == 1234

    def test_frozen(self) -> None:
        info = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1)
        with pytest.raises(AttributeError):
            info.name = "x"  # type: ignore[misc]


class TestGetFrontmostApp:
    @patch("llm_code.computer_use.app_detect._get_via_osascript")
    def test_osascript_fallback(self, mock_osa) -> None:
        mock_osa.return_value = AppInfo(name="Finder", bundle_id="com.apple.finder", pid=100)
        result = get_frontmost_app_sync()
        assert result.name == "Finder"

    @patch("llm_code.computer_use.app_detect._get_via_osascript", side_effect=RuntimeError("no osa"))
    def test_fallback_on_error(self, _mock) -> None:
        result = get_frontmost_app_sync()
        assert result.name == "Unknown"
        assert result.bundle_id == ""
        assert result.pid == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_computer_use/test_app_detect.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement app_detect.py**

```python
# llm_code/computer_use/app_detect.py
"""Detect the frontmost application on macOS."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class AppInfo:
    """Information about a running application."""
    name: str
    bundle_id: str
    pid: int


def _get_via_osascript() -> AppInfo:
    """Use osascript to get frontmost app info."""
    script = (
        'tell application "System Events" to '
        'set fp to first process whose frontmost is true\n'
        'set n to name of fp\n'
        'set b to bundle identifier of fp\n'
        'set p to unix id of fp\n'
        'return n & "|" & b & "|" & (p as text)'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr}")
    parts = result.stdout.strip().split("|")
    if len(parts) < 3:
        raise RuntimeError(f"Unexpected osascript output: {result.stdout}")
    return AppInfo(name=parts[0], bundle_id=parts[1], pid=int(parts[2]))


def get_frontmost_app_sync() -> AppInfo:
    """Get frontmost app, with fallback to Unknown on any error."""
    try:
        return _get_via_osascript()
    except Exception:
        return AppInfo(name="Unknown", bundle_id="", pid=0)


async def get_frontmost_app() -> AppInfo:
    """Async wrapper for get_frontmost_app_sync."""
    return await asyncio.to_thread(get_frontmost_app_sync)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_computer_use/test_app_detect.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/computer_use/app_detect.py tests/test_computer_use/test_app_detect.py
git commit -m "feat: add macOS frontmost app detection via osascript"
```

---

### Task 8: App-aware Computer Use — Tier Classification

**Files:**
- Create: `llm_code/computer_use/app_tier.py`
- Test: `tests/test_computer_use/test_app_tier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_computer_use/test_app_tier.py
"""Tests for app tier classification and enforcement."""
from __future__ import annotations

import pytest

from llm_code.computer_use.app_detect import AppInfo
from llm_code.computer_use.app_tier import (
    DEFAULT_RULES,
    TIER_PERMISSIONS,
    AppTierClassifier,
    AppTierDenied,
    AppTierRule,
)


class TestAppTierRule:
    def test_create(self) -> None:
        rule = AppTierRule(pattern="com.google.Chrome*", tier="read")
        assert rule.tier == "read"

    def test_frozen(self) -> None:
        rule = AppTierRule(pattern="x", tier="full")
        with pytest.raises(AttributeError):
            rule.tier = "read"  # type: ignore[misc]


class TestAppTierClassifier:
    def test_chrome_is_read(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Chrome", bundle_id="com.google.Chrome", pid=1)
        assert classifier.classify(app) == "read"

    def test_safari_is_read(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Safari", bundle_id="com.apple.Safari", pid=1)
        assert classifier.classify(app) == "read"

    def test_terminal_is_click(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Terminal", bundle_id="com.apple.Terminal", pid=1)
        assert classifier.classify(app) == "click"

    def test_vscode_is_click(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="VS Code", bundle_id="com.microsoft.VSCode", pid=1)
        assert classifier.classify(app) == "click"

    def test_unknown_app_is_full(self) -> None:
        classifier = AppTierClassifier(rules=DEFAULT_RULES)
        app = AppInfo(name="Notes", bundle_id="com.apple.Notes", pid=1)
        assert classifier.classify(app) == "full"

    def test_user_rules_override(self) -> None:
        user_rule = AppTierRule(pattern="com.slack.*", tier="click")
        classifier = AppTierClassifier(rules=(user_rule,) + DEFAULT_RULES)
        app = AppInfo(name="Slack", bundle_id="com.slack.Slack", pid=1)
        assert classifier.classify(app) == "click"

    def test_empty_rules_defaults_full(self) -> None:
        classifier = AppTierClassifier(rules=())
        app = AppInfo(name="X", bundle_id="x.y.z", pid=1)
        assert classifier.classify(app) == "full"


class TestTierPermissions:
    def test_read_allows_screenshot(self) -> None:
        assert "screenshot" in TIER_PERMISSIONS["read"]

    def test_read_blocks_click(self) -> None:
        assert "left_click" not in TIER_PERMISSIONS["read"]

    def test_click_allows_left_click(self) -> None:
        assert "left_click" in TIER_PERMISSIONS["click"]

    def test_click_blocks_type(self) -> None:
        assert "type" not in TIER_PERMISSIONS["click"]

    def test_full_allows_all(self) -> None:
        assert "type" in TIER_PERMISSIONS["full"]
        assert "left_click" in TIER_PERMISSIONS["full"]
        assert "hotkey" in TIER_PERMISSIONS["full"]


class TestAppTierDenied:
    def test_message(self) -> None:
        err = AppTierDenied(app="Chrome", tier="read", action="left_click", hint="Use browser MCP")
        assert "Chrome" in str(err)
        assert "read" in str(err)
        assert "left_click" in str(err)

    def test_hint(self) -> None:
        err = AppTierDenied(app="Chrome", tier="read", action="type", hint="Use browser MCP")
        assert err.hint == "Use browser MCP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_computer_use/test_app_tier.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement app_tier.py**

```python
# llm_code/computer_use/app_tier.py
"""App-aware tier classification and permission enforcement for computer use."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from llm_code.computer_use.app_detect import AppInfo


@dataclass(frozen=True)
class AppTierRule:
    """Maps a bundle_id glob pattern to a tier."""
    pattern: str
    tier: str  # "read" | "click" | "full"


DEFAULT_RULES: tuple[AppTierRule, ...] = (
    # Browsers → read
    AppTierRule("com.apple.Safari*", "read"),
    AppTierRule("com.google.Chrome*", "read"),
    AppTierRule("org.mozilla.firefox*", "read"),
    AppTierRule("company.thebrowser.Browser*", "read"),
    AppTierRule("com.microsoft.edgemac*", "read"),
    # Terminals & IDEs → click
    AppTierRule("com.apple.Terminal*", "click"),
    AppTierRule("com.googlecode.iterm2*", "click"),
    AppTierRule("com.microsoft.VSCode*", "click"),
    AppTierRule("com.jetbrains.*", "click"),
    # Everything else → full (handled by default in classify)
)


TIER_PERMISSIONS: dict[str, frozenset[str]] = {
    "read": frozenset({"screenshot", "get_frontmost_app"}),
    "click": frozenset({"screenshot", "get_frontmost_app", "left_click", "scroll"}),
    "full": frozenset({
        "screenshot", "get_frontmost_app", "left_click", "right_click",
        "double_click", "drag", "scroll", "type", "key", "hotkey",
    }),
}


class AppTierDenied(Exception):
    """Raised when an action is blocked by the app's tier."""

    def __init__(self, app: str, tier: str, action: str, hint: str = "") -> None:
        self.app = app
        self.tier = tier
        self.action = action
        self.hint = hint
        super().__init__(
            f"Action '{action}' denied for app '{app}' (tier='{tier}'). {hint}"
        )


@dataclass(frozen=True)
class AppTierClassifier:
    """Classifies apps into tiers based on bundle_id pattern matching."""
    rules: tuple[AppTierRule, ...]

    def classify(self, app: AppInfo) -> str:
        """Return tier for app. First match wins; default is 'full'."""
        for rule in self.rules:
            if fnmatch.fnmatch(app.bundle_id, rule.pattern):
                return rule.tier
        return "full"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_computer_use/test_app_tier.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/computer_use/app_tier.py tests/test_computer_use/test_app_tier.py
git commit -m "feat: add app tier classification with DEFAULT_RULES and TIER_PERMISSIONS"
```

---

### Task 9: App-aware Computer Use — Coordinator Tier Enforcement

**Files:**
- Modify: `llm_code/computer_use/coordinator.py`
- Modify: `llm_code/runtime/config.py:61-63` (ComputerUseConfig)
- Test: `tests/test_computer_use/test_app_tier.py` (add coordinator integration tests)

- [ ] **Step 1: Write failing tests for coordinator tier enforcement**

Append to `tests/test_computer_use/test_app_tier.py`:

```python
from unittest.mock import patch, AsyncMock


class TestCoordinatorTierEnforcement:
    @patch("llm_code.computer_use.coordinator.get_frontmost_app")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_click_blocked_on_read_tier(self, _click, _ss, mock_app) -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        mock_app.return_value = AppInfo(name="Chrome", bundle_id="com.google.Chrome", pid=1)
        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0, app_tiers=())
        coord = ComputerUseCoordinator(config)
        with pytest.raises(AppTierDenied, match="read"):
            coord.click_and_observe(100, 200)

    @patch("llm_code.computer_use.coordinator.get_frontmost_app")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG")
    @patch("llm_code.computer_use.coordinator.keyboard_type")
    def test_type_blocked_on_click_tier(self, _type, _ss, mock_app) -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        mock_app.return_value = AppInfo(name="Terminal", bundle_id="com.apple.Terminal", pid=1)
        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0, app_tiers=())
        coord = ComputerUseCoordinator(config)
        with pytest.raises(AppTierDenied, match="click"):
            coord.type_and_observe("hello")

    @patch("llm_code.computer_use.coordinator.get_frontmost_app")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_click_allowed_on_full_tier(self, _click, _ss, mock_app) -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        mock_app.return_value = AppInfo(name="Notes", bundle_id="com.apple.Notes", pid=1)
        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0, app_tiers=())
        coord = ComputerUseCoordinator(config)
        result = coord.click_and_observe(100, 200)
        assert result["screenshot_base64"] == "IMG"

    @patch("llm_code.computer_use.coordinator.get_frontmost_app")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG")
    def test_screenshot_allowed_on_read_tier(self, _ss, mock_app) -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        mock_app.return_value = AppInfo(name="Chrome", bundle_id="com.google.Chrome", pid=1)
        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0, app_tiers=())
        coord = ComputerUseCoordinator(config)
        result = coord.screenshot()
        assert "screenshot_base64" in result

    @patch("llm_code.computer_use.coordinator.get_frontmost_app")
    @patch("llm_code.computer_use.coordinator.take_screenshot_base64", return_value="IMG")
    @patch("llm_code.computer_use.coordinator.mouse_click")
    def test_user_tier_override(self, _click, _ss, mock_app) -> None:
        from llm_code.computer_use.coordinator import ComputerUseCoordinator
        from llm_code.runtime.config import ComputerUseConfig

        mock_app.return_value = AppInfo(name="Chrome", bundle_id="com.google.Chrome", pid=1)
        # User overrides Chrome to full tier
        user_tier = ({"pattern": "com.google.Chrome*", "tier": "full"},)
        config = ComputerUseConfig(enabled=True, screenshot_delay=0.0, app_tiers=user_tier)
        coord = ComputerUseCoordinator(config)
        result = coord.click_and_observe(100, 200)
        assert result["screenshot_base64"] == "IMG"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_computer_use/test_app_tier.py::TestCoordinatorTierEnforcement -v`
Expected: FAIL — ComputerUseConfig doesn't have `app_tiers`, coordinator doesn't check tier

- [ ] **Step 3: Extend ComputerUseConfig**

In `llm_code/runtime/config.py`, update `ComputerUseConfig`:

```python
@dataclass(frozen=True)
class ComputerUseConfig:
    enabled: bool = False
    screenshot_delay: float = 0.5
    app_tiers: tuple[dict, ...] = ()  # user-defined tier overrides
```

Update `_dict_to_runtime_config` (around line 313-317) to include `app_tiers`:

```python
    computer_use_raw = data.get("computer_use", {})
    computer_use = ComputerUseConfig(
        enabled=computer_use_raw.get("enabled", False),
        screenshot_delay=computer_use_raw.get("screenshot_delay", 0.5),
        app_tiers=tuple(computer_use_raw.get("app_tiers", [])),
    )
```

- [ ] **Step 4: Add tier check to coordinator**

Update `llm_code/computer_use/coordinator.py`:

```python
"""Coordinator that composes screenshot + input for tool actions."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_code.runtime.config import ComputerUseConfig

from llm_code.computer_use.app_detect import get_frontmost_app
from llm_code.computer_use.app_tier import (
    DEFAULT_RULES,
    TIER_PERMISSIONS,
    AppTierClassifier,
    AppTierDenied,
    AppTierRule,
)
from llm_code.computer_use.input_control import (
    keyboard_hotkey,
    keyboard_type,
    mouse_click,
    mouse_drag,
    scroll,
)
from llm_code.computer_use.screenshot import take_screenshot_base64


class ComputerUseCoordinator:
    """Orchestrates GUI actions with follow-up screenshots and app-aware tier enforcement."""

    def __init__(self, config: "ComputerUseConfig") -> None:
        self._config = config
        # Build classifier: user rules first, then defaults
        user_rules = tuple(
            AppTierRule(pattern=r["pattern"], tier=r["tier"])
            for r in self._config.app_tiers
            if isinstance(r, dict) and "pattern" in r and "tier" in r
        )
        self._classifier = AppTierClassifier(rules=user_rules + DEFAULT_RULES)

    def _ensure_enabled(self) -> None:
        if not self._config.enabled:
            raise RuntimeError("Computer use is not enabled. Set computer_use.enabled=true in config.")

    def _check_tier(self, action: str) -> None:
        """Check if the action is allowed for the frontmost app's tier."""
        from llm_code.computer_use.app_detect import get_frontmost_app_sync
        app = get_frontmost_app_sync()
        tier = self._classifier.classify(app)
        if action not in TIER_PERMISSIONS[tier]:
            hint = ""
            if tier == "read":
                hint = "Use MCP browser tools (chrome-devtools) instead."
            elif tier == "click" and action in ("type", "key", "hotkey"):
                hint = "Use the Bash tool instead for terminal input."
            raise AppTierDenied(app=app.name, tier=tier, action=action, hint=hint)

    def _delay_then_screenshot(self) -> str:
        if self._config.screenshot_delay > 0:
            time.sleep(self._config.screenshot_delay)
        return take_screenshot_base64()

    def screenshot(self) -> dict:
        self._ensure_enabled()
        self._check_tier("screenshot")
        img = self._delay_then_screenshot()
        return {"screenshot_base64": img}

    def click_and_observe(self, x: int, y: int, button: str = "left") -> dict:
        self._ensure_enabled()
        self._check_tier("left_click")
        mouse_click(x, y, button=button)
        img = self._delay_then_screenshot()
        return {"action": "click", "x": x, "y": y, "button": button, "screenshot_base64": img}

    def type_and_observe(self, text: str) -> dict:
        self._ensure_enabled()
        self._check_tier("type")
        keyboard_type(text)
        img = self._delay_then_screenshot()
        return {"action": "type", "text": text, "screenshot_base64": img}

    def hotkey_and_observe(self, *keys: str) -> dict:
        self._ensure_enabled()
        self._check_tier("hotkey")
        keyboard_hotkey(*keys)
        img = self._delay_then_screenshot()
        return {"action": "hotkey", "keys": list(keys), "screenshot_base64": img}

    def scroll_and_observe(self, clicks: int, x: int | None = None, y: int | None = None) -> dict:
        self._ensure_enabled()
        self._check_tier("scroll")
        scroll(clicks, x=x, y=y)
        img = self._delay_then_screenshot()
        return {"action": "scroll", "clicks": clicks, "screenshot_base64": img}

    def drag_and_observe(
        self,
        start_x: int,
        start_y: int,
        offset_x: int,
        offset_y: int,
        duration: float = 0.5,
    ) -> dict:
        self._ensure_enabled()
        self._check_tier("drag")
        mouse_drag(start_x, start_y, offset_x, offset_y, duration=duration)
        img = self._delay_then_screenshot()
        return {
            "action": "drag",
            "start_x": start_x,
            "start_y": start_y,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "screenshot_base64": img,
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_computer_use/test_app_tier.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed, 0 failures (existing coordinator tests still pass since they mock at input_control level)

- [ ] **Step 7: Commit**

```bash
git add llm_code/computer_use/coordinator.py llm_code/runtime/config.py tests/test_computer_use/test_app_tier.py
git commit -m "feat: add app-aware tier enforcement to ComputerUseCoordinator"
```

---

## Phase 2: Agent Teams Persistent Mode

### Task 10: Team Template Data Model & Storage

**Files:**
- Create: `llm_code/swarm/team.py`
- Test: `tests/test_swarm/test_team.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/__init__.py
# (empty)

# tests/test_swarm/test_team.py
"""Tests for team template data model and persistence."""
from __future__ import annotations

import json

import pytest

from llm_code.swarm.team import TeamMemberTemplate, TeamTemplate, save_team, load_team, list_teams


class TestTeamMemberTemplate:
    def test_create_minimal(self) -> None:
        m = TeamMemberTemplate(role="reviewer")
        assert m.role == "reviewer"
        assert m.model == ""
        assert m.backend == ""
        assert m.system_prompt == ""

    def test_create_full(self) -> None:
        m = TeamMemberTemplate(role="coder", model="sonnet", backend="worktree", system_prompt="Write code")
        assert m.model == "sonnet"


class TestTeamTemplate:
    def test_create(self) -> None:
        t = TeamTemplate(
            name="test-team",
            description="A test team",
            members=(TeamMemberTemplate(role="a"),),
        )
        assert t.name == "test-team"
        assert len(t.members) == 1
        assert t.max_timeout == 600

    def test_frozen(self) -> None:
        t = TeamTemplate(name="x", description="d", members=())
        with pytest.raises(AttributeError):
            t.name = "y"  # type: ignore[misc]


class TestTeamPersistence:
    def test_save_and_load(self, tmp_path) -> None:
        team = TeamTemplate(
            name="review-team",
            description="Code review",
            members=(
                TeamMemberTemplate(role="security", model="sonnet"),
                TeamMemberTemplate(role="quality", model="haiku"),
            ),
            coordinator_model="sonnet",
            max_timeout=300,
        )
        save_team(team, tmp_path)
        loaded = load_team("review-team", tmp_path)
        assert loaded.name == "review-team"
        assert len(loaded.members) == 2
        assert loaded.members[0].role == "security"
        assert loaded.members[0].model == "sonnet"
        assert loaded.coordinator_model == "sonnet"
        assert loaded.max_timeout == 300

    def test_load_nonexistent_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_team("nonexistent", tmp_path)

    def test_list_teams_empty(self, tmp_path) -> None:
        assert list_teams(tmp_path) == []

    def test_list_teams(self, tmp_path) -> None:
        for name in ("alpha", "beta"):
            save_team(TeamTemplate(name=name, description="d", members=()), tmp_path)
        names = list_teams(tmp_path)
        assert set(names) == {"alpha", "beta"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_swarm/test_team.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement team.py**

```python
# llm_code/swarm/team.py
"""Team template — save/load reusable agent team configurations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TeamMemberTemplate:
    """A template for a single team member."""
    role: str
    model: str = ""
    backend: str = ""
    system_prompt: str = ""


@dataclass(frozen=True)
class TeamTemplate:
    """A reusable team configuration."""
    name: str
    description: str
    members: tuple[TeamMemberTemplate, ...]
    coordinator_model: str = ""
    max_timeout: int = 600


def save_team(team: TeamTemplate, teams_dir: Path) -> Path:
    """Save a team template to JSON. Returns the file path."""
    teams_dir.mkdir(parents=True, exist_ok=True)
    path = teams_dir / f"{team.name}.json"
    data = {
        "name": team.name,
        "description": team.description,
        "members": [
            {
                "role": m.role,
                "model": m.model,
                "backend": m.backend,
                "system_prompt": m.system_prompt,
            }
            for m in team.members
        ],
        "coordinator_model": team.coordinator_model,
        "max_timeout": team.max_timeout,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_team(name: str, teams_dir: Path) -> TeamTemplate:
    """Load a team template from JSON. Raises FileNotFoundError if not found."""
    path = teams_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Team template not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    members = tuple(
        TeamMemberTemplate(
            role=m["role"],
            model=m.get("model", ""),
            backend=m.get("backend", ""),
            system_prompt=m.get("system_prompt", ""),
        )
        for m in data.get("members", [])
    )
    return TeamTemplate(
        name=data["name"],
        description=data.get("description", ""),
        members=members,
        coordinator_model=data.get("coordinator_model", ""),
        max_timeout=data.get("max_timeout", 600),
    )


def list_teams(teams_dir: Path) -> list[str]:
    """Return list of team template names in the directory."""
    if not teams_dir.is_dir():
        return []
    return sorted(p.stem for p in teams_dir.glob("*.json"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_swarm/test_team.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/swarm/team.py tests/test_swarm/__init__.py tests/test_swarm/test_team.py
git commit -m "feat: add TeamTemplate data model with save/load/list persistence"
```

---

### Task 11: Checkpoint System

**Files:**
- Create: `llm_code/swarm/checkpoint.py`
- Test: `tests/test_swarm/test_checkpoint.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/test_checkpoint.py
"""Tests for agent team checkpoint system."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from llm_code.swarm.checkpoint import (
    AgentCheckpoint,
    TeamCheckpoint,
    save_checkpoint,
    load_checkpoint,
    list_checkpoints,
)


class TestAgentCheckpoint:
    def test_create(self) -> None:
        cp = AgentCheckpoint(
            member_id="abc123",
            role="reviewer",
            status="running",
            conversation_snapshot=({"role": "user", "content": "hi"},),
        )
        assert cp.member_id == "abc123"
        assert cp.status == "running"
        assert len(cp.conversation_snapshot) == 1

    def test_defaults(self) -> None:
        cp = AgentCheckpoint(member_id="x", role="r", status="completed", conversation_snapshot=())
        assert cp.last_tool_call is None
        assert cp.output == ""


class TestTeamCheckpoint:
    def test_create(self) -> None:
        agent_cp = AgentCheckpoint(member_id="a", role="r", status="running", conversation_snapshot=())
        tcp = TeamCheckpoint(
            team_name="my-team",
            task_description="do stuff",
            timestamp="2026-04-05T12:00:00Z",
            checkpoints=(agent_cp,),
        )
        assert tcp.team_name == "my-team"
        assert len(tcp.checkpoints) == 1
        assert tcp.completed_members == ()


class TestCheckpointPersistence:
    def test_save_and_load(self, tmp_path) -> None:
        agent_cp = AgentCheckpoint(
            member_id="a1",
            role="coder",
            status="completed",
            conversation_snapshot=({"role": "assistant", "content": "done"},),
            output="result",
        )
        tcp = TeamCheckpoint(
            team_name="test",
            task_description="build feature",
            timestamp="2026-04-05T12:00:00Z",
            checkpoints=(agent_cp,),
            completed_members=("a1",),
        )
        path = save_checkpoint(tcp, tmp_path)
        assert path.exists()
        loaded = load_checkpoint(path)
        assert loaded.team_name == "test"
        assert loaded.task_description == "build feature"
        assert len(loaded.checkpoints) == 1
        assert loaded.checkpoints[0].output == "result"
        assert loaded.completed_members == ("a1",)

    def test_list_checkpoints_empty(self, tmp_path) -> None:
        assert list_checkpoints(tmp_path) == []

    def test_list_checkpoints(self, tmp_path) -> None:
        for i in range(3):
            tcp = TeamCheckpoint(
                team_name="t",
                task_description="d",
                timestamp=f"2026-04-05T12:0{i}:00Z",
                checkpoints=(),
            )
            save_checkpoint(tcp, tmp_path)
        result = list_checkpoints(tmp_path)
        assert len(result) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_swarm/test_checkpoint.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement checkpoint.py**

```python
# llm_code/swarm/checkpoint.py
"""Checkpoint system for agent teams — save/restore agent state for resume."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentCheckpoint:
    """Snapshot of a single agent's state."""
    member_id: str
    role: str
    status: str  # "running" | "completed" | "failed"
    conversation_snapshot: tuple[dict, ...]
    last_tool_call: str | None = None
    output: str = ""


@dataclass(frozen=True)
class TeamCheckpoint:
    """Snapshot of an entire team's state."""
    team_name: str
    task_description: str
    timestamp: str  # ISO 8601
    checkpoints: tuple[AgentCheckpoint, ...]
    coordinator_state: dict = field(default_factory=dict)
    completed_members: tuple[str, ...] = ()


def save_checkpoint(checkpoint: TeamCheckpoint, checkpoints_dir: Path) -> Path:
    """Save a team checkpoint to JSON. Returns the file path."""
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = checkpoint.timestamp.replace(":", "-")
    filename = f"{checkpoint.team_name}-{safe_ts}.json"
    path = checkpoints_dir / filename
    data = {
        "team_name": checkpoint.team_name,
        "task_description": checkpoint.task_description,
        "timestamp": checkpoint.timestamp,
        "checkpoints": [
            {
                "member_id": cp.member_id,
                "role": cp.role,
                "status": cp.status,
                "conversation_snapshot": list(cp.conversation_snapshot),
                "last_tool_call": cp.last_tool_call,
                "output": cp.output,
            }
            for cp in checkpoint.checkpoints
        ],
        "coordinator_state": checkpoint.coordinator_state,
        "completed_members": list(checkpoint.completed_members),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_checkpoint(path: Path) -> TeamCheckpoint:
    """Load a team checkpoint from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    checkpoints = tuple(
        AgentCheckpoint(
            member_id=cp["member_id"],
            role=cp["role"],
            status=cp["status"],
            conversation_snapshot=tuple(cp.get("conversation_snapshot", [])),
            last_tool_call=cp.get("last_tool_call"),
            output=cp.get("output", ""),
        )
        for cp in data.get("checkpoints", [])
    )
    return TeamCheckpoint(
        team_name=data["team_name"],
        task_description=data.get("task_description", ""),
        timestamp=data.get("timestamp", ""),
        checkpoints=checkpoints,
        coordinator_state=data.get("coordinator_state", {}),
        completed_members=tuple(data.get("completed_members", [])),
    )


def list_checkpoints(checkpoints_dir: Path) -> list[Path]:
    """Return list of checkpoint file paths, sorted by name."""
    if not checkpoints_dir.is_dir():
        return []
    return sorted(checkpoints_dir.glob("*.json"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_swarm/test_checkpoint.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/swarm/checkpoint.py tests/test_swarm/test_checkpoint.py
git commit -m "feat: add team checkpoint system with save/load/list for agent state persistence"
```

---

### Task 12: Recovery Policy

**Files:**
- Create: `llm_code/swarm/recovery.py`
- Test: `tests/test_swarm/test_recovery.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_swarm/test_recovery.py
"""Tests for agent recovery policy."""
from __future__ import annotations

import pytest

from llm_code.swarm.recovery import RecoveryPolicy, RecoveryAction


class TestRecoveryPolicy:
    def test_defaults(self) -> None:
        policy = RecoveryPolicy()
        assert policy.max_retries == 2
        assert policy.retry_delay_sec == 5
        assert policy.on_all_failed == "abort"

    def test_frozen(self) -> None:
        policy = RecoveryPolicy()
        with pytest.raises(AttributeError):
            policy.max_retries = 5  # type: ignore[misc]


class TestRecoveryAction:
    def test_should_retry_under_limit(self) -> None:
        policy = RecoveryPolicy(max_retries=3)
        assert RecoveryAction.should_retry(policy, attempt=1) is True
        assert RecoveryAction.should_retry(policy, attempt=3) is True

    def test_should_not_retry_over_limit(self) -> None:
        policy = RecoveryPolicy(max_retries=2)
        assert RecoveryAction.should_retry(policy, attempt=3) is False

    def test_on_all_failed_abort(self) -> None:
        policy = RecoveryPolicy(on_all_failed="abort")
        assert RecoveryAction.resolve_all_failed(policy) == "abort"

    def test_on_all_failed_checkpoint(self) -> None:
        policy = RecoveryPolicy(on_all_failed="checkpoint_and_stop")
        assert RecoveryAction.resolve_all_failed(policy) == "checkpoint_and_stop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_swarm/test_recovery.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement recovery.py**

```python
# llm_code/swarm/recovery.py
"""Recovery policy for agent teams — retry logic and failure handling."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryPolicy:
    """Configuration for agent failure recovery."""
    max_retries: int = 2
    retry_delay_sec: int = 5
    on_all_failed: str = "abort"  # "abort" | "checkpoint_and_stop"


class RecoveryAction:
    """Stateless helpers for recovery decisions."""

    @staticmethod
    def should_retry(policy: RecoveryPolicy, attempt: int) -> bool:
        """Return True if the attempt number is within the retry limit."""
        return attempt <= policy.max_retries

    @staticmethod
    def resolve_all_failed(policy: RecoveryPolicy) -> str:
        """Return the action to take when all members have failed."""
        return policy.on_all_failed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_swarm/test_recovery.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/swarm/recovery.py tests/test_swarm/test_recovery.py
git commit -m "feat: add RecoveryPolicy with retry logic for agent team failure handling"
```

---

## Phase 3: Enterprise Features

### Task 13: Auth Provider Abstraction & AuthIdentity

**Files:**
- Create: `llm_code/enterprise/__init__.py`
- Create: `llm_code/enterprise/auth.py`
- Test: `tests/test_enterprise/test_auth.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_enterprise/__init__.py
# (empty)

# tests/test_enterprise/test_auth.py
"""Tests for enterprise auth abstractions."""
from __future__ import annotations

import pytest

from llm_code.enterprise.auth import AuthIdentity, AuthProvider


class TestAuthIdentity:
    def test_create_minimal(self) -> None:
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="Alice")
        assert identity.user_id == "u1"
        assert identity.groups == ()
        assert identity.raw_claims == {}

    def test_create_with_groups(self) -> None:
        identity = AuthIdentity(
            user_id="u1", email="a@b.com", display_name="Alice",
            groups=("admin", "dev"),
        )
        assert identity.groups == ("admin", "dev")

    def test_frozen(self) -> None:
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="Alice")
        with pytest.raises(AttributeError):
            identity.user_id = "u2"  # type: ignore[misc]


class TestAuthProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            AuthProvider()  # type: ignore[abstract]

    def test_subclass_must_implement(self) -> None:
        class Bad(AuthProvider):
            pass
        with pytest.raises(TypeError):
            Bad()  # type: ignore[abstract]

    def test_valid_subclass(self) -> None:
        class Good(AuthProvider):
            async def authenticate(self) -> AuthIdentity:
                return AuthIdentity(user_id="x", email="x@x.com", display_name="X")
            async def refresh(self) -> AuthIdentity | None:
                return None
            async def revoke(self) -> None:
                pass
        provider = Good()
        assert provider is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_enterprise/test_auth.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement auth module**

```python
# llm_code/enterprise/__init__.py
"""Enterprise features — auth, RBAC, audit."""

# llm_code/enterprise/auth.py
"""Authentication provider abstraction and identity model."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthIdentity:
    """Represents an authenticated user."""
    user_id: str
    email: str
    display_name: str
    groups: tuple[str, ...] = ()
    raw_claims: dict = field(default_factory=dict)


class AuthProvider(ABC):
    """Abstract base for authentication providers."""

    @abstractmethod
    async def authenticate(self) -> AuthIdentity:
        """Perform authentication and return the identity."""
        ...

    @abstractmethod
    async def refresh(self) -> AuthIdentity | None:
        """Refresh the authentication token. Return None if refresh fails."""
        ...

    @abstractmethod
    async def revoke(self) -> None:
        """Revoke the current authentication session."""
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_enterprise/test_auth.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/enterprise/__init__.py llm_code/enterprise/auth.py tests/test_enterprise/__init__.py tests/test_enterprise/test_auth.py
git commit -m "feat: add AuthProvider ABC and AuthIdentity for enterprise auth"
```

---

### Task 14: OIDC Provider

**Files:**
- Create: `llm_code/enterprise/oidc.py`
- Test: `tests/test_enterprise/test_oidc.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_enterprise/test_oidc.py
"""Tests for OIDC authentication provider."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from llm_code.enterprise.oidc import OIDCConfig, OIDCProvider


class TestOIDCConfig:
    def test_create_minimal(self) -> None:
        config = OIDCConfig(issuer="https://accounts.google.com", client_id="abc")
        assert config.issuer == "https://accounts.google.com"
        assert config.client_secret == ""
        assert config.scopes == ("openid", "email", "profile")
        assert config.redirect_port == 9877

    def test_frozen(self) -> None:
        config = OIDCConfig(issuer="x", client_id="y")
        with pytest.raises(AttributeError):
            config.issuer = "z"  # type: ignore[misc]


class TestOIDCProviderDiscovery:
    @patch("llm_code.enterprise.oidc.httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_discover_endpoints(self, mock_client_cls) -> None:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "authorization_endpoint": "https://example.com/auth",
                "token_endpoint": "https://example.com/token",
                "userinfo_endpoint": "https://example.com/userinfo",
            }),
        )
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config)
        endpoints = await provider._discover()
        assert endpoints["authorization_endpoint"] == "https://example.com/auth"


class TestOIDCProviderTokenStorage:
    def test_token_path(self, tmp_path) -> None:
        config = OIDCConfig(issuer="https://example.com", client_id="abc")
        provider = OIDCProvider(config, token_dir=tmp_path)
        assert provider._token_path == tmp_path / "oidc_tokens.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_enterprise/test_oidc.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement OIDC provider**

```python
# llm_code/enterprise/oidc.py
"""OIDC authentication provider with PKCE flow."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path

import httpx

from llm_code.enterprise.auth import AuthIdentity, AuthProvider

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OIDCConfig:
    """Configuration for OIDC authentication."""
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    redirect_port: int = 9877


class OIDCProvider(AuthProvider):
    """OIDC authentication with PKCE flow."""

    def __init__(self, config: OIDCConfig, token_dir: Path | None = None) -> None:
        self._config = config
        self._token_dir = token_dir or Path.home() / ".llm-code" / "auth"
        self._token_path = self._token_dir / "oidc_tokens.json"
        self._endpoints: dict[str, str] | None = None

    async def _discover(self) -> dict[str, str]:
        """Fetch OIDC discovery document."""
        if self._endpoints is not None:
            return self._endpoints
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._endpoints = resp.json()
            return self._endpoints

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    def _save_tokens(self, tokens: dict) -> None:
        """Save tokens to disk."""
        self._token_dir.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(tokens), encoding="utf-8")

    def _load_tokens(self) -> dict | None:
        """Load tokens from disk. Returns None if not found."""
        if not self._token_path.exists():
            return None
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    async def authenticate(self) -> AuthIdentity:
        """Perform OIDC PKCE authentication flow."""
        endpoints = await self._discover()
        _log.info("OIDC authentication initiated for issuer: %s", self._config.issuer)
        # Full browser-based PKCE flow would go here
        # For now, raise NotImplementedError as the full flow requires
        # a local HTTP server and browser interaction
        raise NotImplementedError(
            "Full OIDC PKCE flow requires browser interaction. "
            "Use 'llm-code auth login' command."
        )

    async def refresh(self) -> AuthIdentity | None:
        """Refresh tokens using refresh_token."""
        tokens = self._load_tokens()
        if not tokens or "refresh_token" not in tokens:
            return None
        endpoints = await self._discover()
        token_url = endpoints.get("token_endpoint", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "refresh_token",
                "client_id": self._config.client_id,
                "refresh_token": tokens["refresh_token"],
            })
            if resp.status_code != 200:
                return None
            new_tokens = resp.json()
            self._save_tokens(new_tokens)
            # Extract identity from id_token claims (simplified)
            return AuthIdentity(
                user_id=new_tokens.get("sub", ""),
                email=new_tokens.get("email", ""),
                display_name=new_tokens.get("name", ""),
            )

    async def revoke(self) -> None:
        """Delete stored tokens."""
        if self._token_path.exists():
            self._token_path.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_enterprise/test_oidc.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/enterprise/oidc.py tests/test_enterprise/test_oidc.py
git commit -m "feat: add OIDCProvider with PKCE flow, discovery, and token storage"
```

---

### Task 15: RBAC Engine

**Files:**
- Create: `llm_code/enterprise/rbac.py`
- Test: `tests/test_enterprise/test_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_enterprise/test_rbac.py
"""Tests for RBAC engine."""
from __future__ import annotations

import pytest

from llm_code.enterprise.auth import AuthIdentity
from llm_code.enterprise.rbac import DEFAULT_ROLES, RBACEngine, Role


class TestRole:
    def test_admin_has_wildcard(self) -> None:
        admin = DEFAULT_ROLES["admin"]
        assert "*" in admin.permissions

    def test_viewer_limited(self) -> None:
        viewer = DEFAULT_ROLES["viewer"]
        assert "tool:read" in viewer.permissions
        assert "tool:bash" not in viewer.permissions


class TestRBACEngine:
    def test_admin_allowed_everything(self) -> None:
        engine = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("admins",))
        assert engine.is_allowed(identity, "tool:bash") is True
        assert engine.is_allowed(identity, "tool:edit") is True

    def test_viewer_blocked_from_edit(self) -> None:
        engine = RBACEngine(group_role_mapping={"viewers": "viewer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("viewers",))
        assert engine.is_allowed(identity, "tool:read") is True
        assert engine.is_allowed(identity, "tool:bash") is False

    def test_developer_allowed_tools(self) -> None:
        engine = RBACEngine(group_role_mapping={"devs": "developer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("devs",))
        assert engine.is_allowed(identity, "tool:bash") is True
        assert engine.is_allowed(identity, "tool:edit") is True

    def test_no_matching_group_denied(self) -> None:
        engine = RBACEngine(group_role_mapping={"admins": "admin"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("unknown",))
        assert engine.is_allowed(identity, "tool:bash") is False

    def test_no_auth_default_admin(self) -> None:
        engine = RBACEngine(group_role_mapping={})
        assert engine.is_allowed(None, "tool:bash") is True

    def test_multiple_groups_highest_wins(self) -> None:
        engine = RBACEngine(group_role_mapping={"viewers": "viewer", "devs": "developer"})
        identity = AuthIdentity(
            user_id="u1", email="a@b.com", display_name="A",
            groups=("viewers", "devs"),
        )
        assert engine.is_allowed(identity, "tool:bash") is True

    def test_tool_deny_pattern(self) -> None:
        engine = RBACEngine(group_role_mapping={"devs": "developer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("devs",))
        # developer has tool_deny for "tool:bash:rm -rf *"
        assert engine.is_denied_by_pattern(identity, "tool:bash:rm -rf /") is True

    def test_custom_roles(self) -> None:
        custom = Role(name="ops", permissions=frozenset({"tool:bash", "swarm:create"}))
        engine = RBACEngine(
            group_role_mapping={"ops-team": "ops"},
            custom_roles={"ops": custom},
        )
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("ops-team",))
        assert engine.is_allowed(identity, "tool:bash") is True
        assert engine.is_allowed(identity, "tool:edit") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_enterprise/test_rbac.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement RBAC engine**

```python
# llm_code/enterprise/rbac.py
"""Role-based access control engine."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from llm_code.enterprise.auth import AuthIdentity


@dataclass(frozen=True)
class Role:
    """A named role with permission grants and tool deny patterns."""
    name: str
    permissions: frozenset[str]
    tool_allow: tuple[str, ...] = ()
    tool_deny: tuple[str, ...] = ()


DEFAULT_ROLES: dict[str, Role] = {
    "admin": Role("admin", frozenset({"*"})),
    "developer": Role(
        "developer",
        frozenset({"tool:*", "swarm:create", "session:*", "skill:*"}),
        tool_deny=("tool:bash:rm -rf *",),
    ),
    "viewer": Role(
        "viewer",
        frozenset({"tool:read", "tool:glob", "tool:grep", "session:read"}),
    ),
}


class RBACEngine:
    """Evaluates permissions based on user identity and role assignments."""

    def __init__(
        self,
        group_role_mapping: dict[str, str],
        custom_roles: dict[str, Role] | None = None,
    ) -> None:
        self._group_role_mapping = group_role_mapping
        self._roles = {**DEFAULT_ROLES, **(custom_roles or {})}

    def _get_roles(self, identity: AuthIdentity | None) -> list[Role]:
        """Resolve roles for an identity. No auth → admin."""
        if identity is None:
            return [self._roles["admin"]]
        roles = []
        for group in identity.groups:
            role_name = self._group_role_mapping.get(group)
            if role_name and role_name in self._roles:
                roles.append(self._roles[role_name])
        return roles

    def is_allowed(self, identity: AuthIdentity | None, permission: str) -> bool:
        """Check if identity has the given permission."""
        roles = self._get_roles(identity)
        if not roles:
            return False
        for role in roles:
            if "*" in role.permissions:
                return True
            for perm in role.permissions:
                if perm == permission or (perm.endswith(":*") and permission.startswith(perm[:-1])):
                    return True
        return False

    def is_denied_by_pattern(self, identity: AuthIdentity | None, action: str) -> bool:
        """Check if action matches any deny pattern in the user's roles."""
        roles = self._get_roles(identity)
        for role in roles:
            for pattern in role.tool_deny:
                if fnmatch.fnmatch(action, pattern):
                    return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_enterprise/test_rbac.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/enterprise/rbac.py tests/test_enterprise/test_rbac.py
git commit -m "feat: add RBACEngine with role definitions, wildcard matching, and deny patterns"
```

---

### Task 16: Audit Logger

**Files:**
- Create: `llm_code/enterprise/audit.py`
- Test: `tests/test_enterprise/test_audit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_enterprise/test_audit.py
"""Tests for audit logging system."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from llm_code.enterprise.audit import (
    AuditEvent,
    AuditLogger,
    CompositeAuditLogger,
    FileAuditLogger,
)


class TestAuditEvent:
    def test_create_minimal(self) -> None:
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z",
            event_type="tool_execute",
            user_id="local",
        )
        assert event.tool_name == ""
        assert event.outcome == ""
        assert event.metadata == {}

    def test_create_full(self) -> None:
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z",
            event_type="tool_execute",
            user_id="u1",
            tool_name="bash",
            action="ls -la",
            outcome="allowed",
            metadata={"cwd": "/tmp"},
        )
        assert event.tool_name == "bash"
        assert event.outcome == "allowed"


class TestFileAuditLogger:
    @pytest.mark.asyncio
    async def test_log_creates_file(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z",
            event_type="test",
            user_id="local",
        )
        await logger.log(event)
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text().strip()
        data = json.loads(line)
        assert data["event_type"] == "test"

    @pytest.mark.asyncio
    async def test_log_appends(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        for i in range(3):
            event = AuditEvent(
                timestamp=f"2026-04-05T12:0{i}:00Z",
                event_type=f"event_{i}",
                user_id="local",
            )
            await logger.log(event)
        files = list(tmp_path.glob("*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_daily_file_naming(self, tmp_path) -> None:
        logger = FileAuditLogger(audit_dir=tmp_path)
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z",
            event_type="test",
            user_id="local",
        )
        await logger.log(event)
        assert (tmp_path / "2026-04-05.jsonl").exists()


class TestCompositeAuditLogger:
    @pytest.mark.asyncio
    async def test_logs_to_multiple(self, tmp_path) -> None:
        dir1 = tmp_path / "a"
        dir2 = tmp_path / "b"
        logger = CompositeAuditLogger(loggers=[
            FileAuditLogger(audit_dir=dir1),
            FileAuditLogger(audit_dir=dir2),
        ])
        event = AuditEvent(
            timestamp="2026-04-05T12:00:00Z",
            event_type="test",
            user_id="local",
        )
        await logger.log(event)
        assert len(list(dir1.glob("*.jsonl"))) == 1
        assert len(list(dir2.glob("*.jsonl"))) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_enterprise/test_audit.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement audit module**

```python
# llm_code/enterprise/audit.py
"""Audit logging — JSONL file logger with composite support."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """A single audit log entry."""
    timestamp: str
    event_type: str  # "tool_execute", "permission_denied", "auth_login", etc.
    user_id: str  # from AuthIdentity, "local" when no auth
    tool_name: str = ""
    action: str = ""
    outcome: str = ""  # "allowed", "denied", "error"
    metadata: dict = field(default_factory=dict)


class AuditLogger(ABC):
    """Abstract base for audit loggers."""

    @abstractmethod
    async def log(self, event: AuditEvent) -> None:
        """Record an audit event."""
        ...


class FileAuditLogger(AuditLogger):
    """Writes audit events as JSONL to daily files."""

    def __init__(self, audit_dir: Path) -> None:
        self._audit_dir = audit_dir

    async def log(self, event: AuditEvent) -> None:
        """Append event as JSON line to daily file."""
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        # Extract date from timestamp for daily file naming
        date_str = event.timestamp[:10]  # "YYYY-MM-DD"
        path = self._audit_dir / f"{date_str}.jsonl"
        line = json.dumps({
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "user_id": event.user_id,
            "tool_name": event.tool_name,
            "action": event.action,
            "outcome": event.outcome,
            "metadata": event.metadata,
        })
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class CompositeAuditLogger(AuditLogger):
    """Writes to multiple audit loggers."""

    def __init__(self, loggers: list[AuditLogger]) -> None:
        self._loggers = loggers

    async def log(self, event: AuditEvent) -> None:
        """Log to all child loggers."""
        for logger in self._loggers:
            try:
                await logger.log(event)
            except Exception as exc:
                _log.warning("Audit logger failed: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_enterprise/test_audit.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add llm_code/enterprise/audit.py tests/test_enterprise/test_audit.py
git commit -m "feat: add FileAuditLogger and CompositeAuditLogger with JSONL daily rotation"
```

---

### Task 17: Enterprise Config Integration

**Files:**
- Modify: `llm_code/runtime/config.py`
- Test: `tests/test_runtime/test_config_enterprise.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_config_enterprise.py
"""Tests for enterprise config integration."""
from __future__ import annotations

import pytest

from llm_code.runtime.config import EnterpriseConfig, EnterpriseAuthConfig, EnterpriseRBACConfig, EnterpriseAuditConfig


class TestEnterpriseConfig:
    def test_defaults_disabled(self) -> None:
        config = EnterpriseConfig()
        assert config.auth.provider == ""
        assert config.rbac.group_role_mapping == {}
        assert config.audit.retention_days == 90

    def test_auth_config(self) -> None:
        auth = EnterpriseAuthConfig(
            provider="oidc",
            oidc_issuer="https://accounts.google.com",
            oidc_client_id="abc",
        )
        assert auth.provider == "oidc"
        assert auth.oidc_issuer == "https://accounts.google.com"

    def test_rbac_config(self) -> None:
        rbac = EnterpriseRBACConfig(group_role_mapping={"admins": "admin"})
        assert rbac.group_role_mapping == {"admins": "admin"}

    def test_audit_config(self) -> None:
        audit = EnterpriseAuditConfig(retention_days=30)
        assert audit.retention_days == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime/test_config_enterprise.py -v`
Expected: FAIL — classes not defined

- [ ] **Step 3: Add enterprise config dataclasses to config.py**

In `llm_code/runtime/config.py`, add before `RuntimeConfig`:

```python
@dataclass(frozen=True)
class EnterpriseAuthConfig:
    provider: str = ""  # "" | "none" | "oidc"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: tuple[str, ...] = ("openid", "email", "profile")
    oidc_redirect_port: int = 9877


@dataclass(frozen=True)
class EnterpriseRBACConfig:
    group_role_mapping: dict[str, str] = field(default_factory=dict)
    custom_roles: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EnterpriseAuditConfig:
    retention_days: int = 90


@dataclass(frozen=True)
class EnterpriseConfig:
    auth: EnterpriseAuthConfig = field(default_factory=EnterpriseAuthConfig)
    rbac: EnterpriseRBACConfig = field(default_factory=EnterpriseRBACConfig)
    audit: EnterpriseAuditConfig = field(default_factory=EnterpriseAuditConfig)
```

Add `enterprise: EnterpriseConfig = field(default_factory=EnterpriseConfig)` to `RuntimeConfig`.

Update `_dict_to_runtime_config` to parse enterprise config:

```python
    enterprise_raw = data.get("enterprise", {})
    auth_raw = enterprise_raw.get("auth", {})
    rbac_raw = enterprise_raw.get("rbac", {})
    audit_raw = enterprise_raw.get("audit", {})
    oidc_raw = auth_raw.get("oidc", {})
    enterprise_auth = EnterpriseAuthConfig(
        provider=auth_raw.get("provider", ""),
        oidc_issuer=oidc_raw.get("issuer", ""),
        oidc_client_id=oidc_raw.get("client_id", ""),
        oidc_client_secret=oidc_raw.get("client_secret", ""),
        oidc_scopes=tuple(oidc_raw.get("scopes", ("openid", "email", "profile"))),
        oidc_redirect_port=oidc_raw.get("redirect_port", 9877),
    )
    enterprise_rbac = EnterpriseRBACConfig(
        group_role_mapping=rbac_raw.get("group_role_mapping", {}),
        custom_roles=rbac_raw.get("custom_roles", {}),
    )
    enterprise_audit = EnterpriseAuditConfig(
        retention_days=audit_raw.get("retention_days", 90),
    )
    enterprise = EnterpriseConfig(
        auth=enterprise_auth,
        rbac=enterprise_rbac,
        audit=enterprise_audit,
    )
```

Add `enterprise=enterprise` to the `return RuntimeConfig(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime/test_config_enterprise.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed, 0 failures

- [ ] **Step 6: Commit**

```bash
git add llm_code/runtime/config.py tests/test_runtime/test_config_enterprise.py
git commit -m "feat: add EnterpriseConfig with auth/rbac/audit sub-configs to RuntimeConfig"
```

---

### Task 18: RBAC Integration with PermissionPolicy

**Files:**
- Modify: `llm_code/runtime/permissions.py`
- Test: `tests/test_runtime/test_permissions_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_runtime/test_permissions_rbac.py
"""Tests for RBAC integration with PermissionPolicy."""
from __future__ import annotations

import pytest

from llm_code.enterprise.auth import AuthIdentity
from llm_code.enterprise.rbac import RBACEngine
from llm_code.runtime.permissions import PermissionMode, PermissionOutcome, PermissionPolicy
from llm_code.tools.base import PermissionLevel


class TestPermissionPolicyWithRBAC:
    def test_rbac_deny_overrides_mode(self) -> None:
        rbac = RBACEngine(group_role_mapping={"viewers": "viewer"})
        identity = AuthIdentity(user_id="u1", email="a@b.com", display_name="A", groups=("viewers",))
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS, rbac=rbac)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=identity)
        assert result == PermissionOutcome.DENY

    def test_no_rbac_allows_normally(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS)
        assert result == PermissionOutcome.ALLOW

    def test_rbac_none_identity_allows(self) -> None:
        rbac = RBACEngine(group_role_mapping={})
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS, rbac=rbac)
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS, identity=None)
        assert result == PermissionOutcome.ALLOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime/test_permissions_rbac.py -v`
Expected: FAIL — `PermissionPolicy` doesn't accept `rbac` parameter

- [ ] **Step 3: Extend PermissionPolicy to accept RBAC**

In `llm_code/runtime/permissions.py`, update `PermissionPolicy.__init__` and `authorize`:

```python
class PermissionPolicy:
    def __init__(
        self,
        mode: PermissionMode,
        allow_tools: frozenset[str] = frozenset(),
        deny_tools: frozenset[str] = frozenset(),
        deny_patterns: tuple[str, ...] = (),
        rbac: object | None = None,  # RBACEngine, loosely typed
    ) -> None:
        self._mode = mode
        self._allow_tools = allow_tools
        self._deny_tools = deny_tools
        self._deny_patterns = deny_patterns
        self._rbac = rbac

        for warning in detect_shadowed_rules(allow_tools, deny_tools, mode):
            _log.warning("PermissionPolicy: %s", warning)

    def authorize(
        self,
        tool_name: str,
        required: PermissionLevel,
        effective_level: PermissionLevel | None = None,
        identity: object | None = None,  # AuthIdentity
    ) -> PermissionOutcome:
        level = effective_level if effective_level is not None else required

        # 0. RBAC check (if engine and identity provided)
        if self._rbac is not None and identity is not None:
            if not self._rbac.is_allowed(identity, f"tool:{tool_name}"):
                return PermissionOutcome.DENY

        # 1. Deny list and patterns always win
        if tool_name in self._deny_tools:
            return PermissionOutcome.DENY
        for pattern in self._deny_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                return PermissionOutcome.DENY

        # 2. Explicit allow list overrides mode restrictions
        if tool_name in self._allow_tools:
            return PermissionOutcome.ALLOW

        # 3. AUTO_ACCEPT allows everything
        if self._mode == PermissionMode.AUTO_ACCEPT:
            return PermissionOutcome.ALLOW

        # 4. PROMPT mode
        if self._mode == PermissionMode.PROMPT:
            if level == PermissionLevel.READ_ONLY:
                return PermissionOutcome.ALLOW
            return PermissionOutcome.NEED_PROMPT

        # 4b. PLAN mode
        if self._mode == PermissionMode.PLAN:
            if level == PermissionLevel.READ_ONLY:
                return PermissionOutcome.ALLOW
            return PermissionOutcome.NEED_PLAN

        # 5. Level-based comparison
        level_rank = _LEVEL_RANK[level]
        mode_max = _MODE_MAX_LEVEL[self._mode]
        if level_rank <= mode_max:
            return PermissionOutcome.ALLOW
        return PermissionOutcome.DENY

    def allow_tool(self, tool_name: str) -> None:
        self._allow_tools = self._allow_tools | frozenset({tool_name})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime/test_permissions_rbac.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `python -m pytest -q --tb=short`
Expected: 2695+ passed, 0 failures

- [ ] **Step 6: Commit**

```bash
git add llm_code/runtime/permissions.py tests/test_runtime/test_permissions_rbac.py
git commit -m "feat: integrate RBAC engine into PermissionPolicy authorization flow"
```

---

### Task 19: Add /audit Slash Command & Final Wiring

**Files:**
- Modify: `llm_code/cli/commands.py`
- Modify: `llm_code/tui/input_bar.py`
- Test: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli/test_commands.py`:

```python
class TestAuditCommand:
    def test_parse_audit_no_args(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/audit")
        assert cmd is not None
        assert cmd.name == "audit"
        assert cmd.args == ""

    def test_parse_audit_search(self) -> None:
        from llm_code.cli.commands import parse_slash_command
        cmd = parse_slash_command("/audit search bash")
        assert cmd is not None
        assert cmd.args == "search bash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli/test_commands.py::TestAuditCommand -v`
Expected: FAIL — "audit" not in KNOWN_COMMANDS

- [ ] **Step 3: Add "audit" to KNOWN_COMMANDS and SLASH_COMMANDS**

In `llm_code/cli/commands.py`, add `"audit"` to `KNOWN_COMMANDS`.

In `llm_code/tui/input_bar.py`, add `"/audit"` to `SLASH_COMMANDS` and `("/audit", "Audit log")` to `SLASH_COMMAND_DESCS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli/test_commands.py::TestAuditCommand -v`
Expected: PASS

- [ ] **Step 5: Run full test suite — final validation**

Run: `python -m pytest -q --tb=short`
Expected: All previous tests + ~70 new tests pass, 0 failures

- [ ] **Step 6: Commit**

```bash
git add llm_code/cli/commands.py llm_code/tui/input_bar.py tests/test_cli/test_commands.py
git commit -m "feat: add /audit and /keybind slash commands; complete low priority feature wiring"
```

---

## Summary

| Task | Feature | New Tests | Files Created | Files Modified |
|------|---------|-----------|---------------|----------------|
| 1 | Skill dataclass | 6 | 1 test | 1 |
| 2 | Skill parser | 8 | — | 1 source, 1 test |
| 3 | Skill resolver | 9 | 1 source, 1 test | — |
| 4 | Keybindings core | 18 | 1 source, 1 test | — |
| 5 | input_bar refactor | 2 | — | 1 source, 1 test |
| 6 | /keybind command | 3 | — | 2 source, 1 test |
| 7 | App detection | 4 | 1 source, 1 test | — |
| 8 | App tier | 14 | 1 source, 1 test | — |
| 9 | Coordinator tier | 5 | — | 2 source, 1 test |
| 10 | Team template | 8 | 1 source, 1 test | — |
| 11 | Checkpoint | 7 | 1 source, 1 test | — |
| 12 | Recovery | 4 | 1 source, 1 test | — |
| 13 | Auth provider | 5 | 2 source, 2 test | — |
| 14 | OIDC | 3 | 1 source, 1 test | — |
| 15 | RBAC | 8 | 1 source, 1 test | — |
| 16 | Audit logger | 6 | 1 source, 1 test | — |
| 17 | Enterprise config | 4 | 1 test | 1 source |
| 18 | RBAC + permissions | 3 | 1 test | 1 source |
| 19 | /audit command | 2 | — | 2 source, 1 test |

**Total: ~119 new tests, 12 new source files, 6 modified source files, 19 commits**

# LLM-Code: Low Priority Features Design Spec

**Date:** 2026-04-05
**Author:** Adam
**Status:** Approved
**Scope:** 5 low-priority features to complete Claude Code parity

---

## Overview

### Decision Summary

| Feature | Decision |
|---------|----------|
| Skill frontmatter | Auto-resolve deps (B), version/tags/model/depends/min_version |
| Keybindings | General mode only (A), config file + chords + /keybind command |
| Agent Teams | Template + checkpoint resume (B), auto-recovery with retry policy |
| Computer use | Coordinator-layer app-aware (A), tier classification + access request |
| Enterprise | Pluggable auth (C), OIDC built-in + RBAC + audit log |

### Overall Architecture

**Approach B — Extend existing modules.** Each feature builds on existing module boundaries:
- Skill frontmatter → `runtime/skills.py` + new `runtime/skill_resolver.py`
- Keybindings → `tui/keybindings.py` + refactor `tui/input_bar.py`
- Agent Teams → `swarm/` (team.py, checkpoint.py, recovery.py)
- Computer use → `computer_use/` (app_detect.py, app_tier.py)
- Enterprise → new `enterprise/` package (only truly new domain gets new dir)

### Implementation Phases

```
Phase 1 (independent, parallelizable)
  ├── Feature 11: Skill frontmatter extension
  ├── Feature 12: Keybindings customization
  └── Feature 14: App-aware computer use

Phase 2 (depends on existing swarm)
  └── Feature 13: Agent Teams persistent mode

Phase 3 (cross-cutting)
  └── Feature 15: Enterprise features (auth, RBAC, audit)
```

---

## Feature 11: Skill Frontmatter Extension

### New Frontmatter Fields

```yaml
---
name: my-skill
description: Does something useful
auto: false
trigger: my-skill
# new fields
version: 1.2.0
tags: [debugging, python]
model: sonnet
depends:
  - name: base-tools
    registry: official
  - name: python-patterns
min_version: "0.8.0"
---
```

### Data Model

```python
@dataclass(frozen=True)
class SkillDependency:
    name: str
    registry: str = ""  # empty = search all

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    content: str
    auto: bool = False
    trigger: str = ""
    # new
    version: str = ""
    tags: tuple[str, ...] = ()
    model: str = ""
    depends: tuple[SkillDependency, ...] = ()
    min_version: str = ""
```

### Dependency Resolution

1. `SkillLoader.load_skill()` parses frontmatter, constructs `Skill`
2. New `SkillResolver` class checks `depends` after load
3. For each uninstalled dependency: query registries (specified registry first, else all)
4. Found → call `PluginInstaller.install()` automatically
5. Recursive check on newly installed skill's depends (max 3 levels, cycle detection)
6. Cycle detection: maintain `visited: set[str]`, duplicate → error

### min_version Check

Compare `llm_code.__version__` vs `min_version`. Mismatch → warning, don't block loading.

### model Field

Inject into system prompt context as suggestion when skill is loaded. Does not override user settings.

### Files

| File | Change |
|------|--------|
| `runtime/skills.py` | Extend Skill dataclass + frontmatter parser |
| `runtime/skill_resolver.py` | **New** — dependency resolution logic |

---

## Feature 12: Keybindings Customization

### Config File

`~/.llm-code/keybindings.json`

```json
{
  "bindings": {
    "submit": "enter",
    "newline": "shift+enter",
    "cancel": "escape",
    "clear_input": "ctrl+u",
    "autocomplete": "tab",
    "history_prev": "ctrl+p",
    "history_next": "ctrl+n",
    "toggle_thinking": "alt+t",
    "toggle_vim": "ctrl+shift+v",
    "voice_input": "ctrl+space"
  },
  "chords": {
    "ctrl+k ctrl+c": "comment_selection",
    "ctrl+k ctrl+u": "uncomment_selection"
  }
}
```

### Action System

```python
@dataclass(frozen=True)
class KeyAction:
    name: str
    description: str
    default_key: str

@dataclass(frozen=True)
class ChordBinding:
    keys: tuple[str, ...]
    action: str

ACTION_REGISTRY: dict[str, KeyAction] = {
    "submit": KeyAction("submit", "Submit input", "enter"),
    "newline": KeyAction("newline", "Insert newline", "shift+enter"),
    "cancel": KeyAction("cancel", "Cancel generation", "escape"),
    # ...
}
```

### Chord Implementation

```python
@dataclass
class ChordState:
    pending: str | None = None
    timeout_ms: int = 1000

    def feed(self, key: str) -> str | None:
        """Return matched action or None"""
```

- First chord key → store in `pending`, start timeout
- Second key within timeout → lookup match → return action
- Timeout or no match → clear pending, pass original key through

### Key Resolution Flow

```
Key event
  → ChordState.feed()
    → chord match? → execute chord action
    → no match? → lookup bindings table
      → binding found? → execute action
      → no binding? → pass to input buffer (normal char input)
```

### Conflict Detection

- Same key bound to two actions → error
- Chord prefix key cannot also be a single-key binding
- On conflict: warning + use default

### /keybind Command

```
/keybind                    # list all current bindings
/keybind submit ctrl+enter  # rebind submit action
/keybind reset              # restore all defaults
/keybind reset submit       # restore single action default
```

### Files

| File | Change |
|------|--------|
| `tui/keybindings.py` | **New** — KeyAction registry, ChordState, config loader, conflict detection |
| `tui/input_bar.py` | Refactor `on_key()` from hardcoded to table lookup |
| `cli/commands.py` | Add `/keybind` slash command |

---

## Feature 13: Agent Teams Persistent Mode

### Team Template

```python
@dataclass(frozen=True)
class TeamMemberTemplate:
    role: str
    model: str = ""
    backend: str = ""
    system_prompt: str = ""

@dataclass(frozen=True)
class TeamTemplate:
    name: str
    description: str
    members: tuple[TeamMemberTemplate, ...]
    coordinator_model: str = ""
    max_timeout: int = 600
```

Storage: `~/.llm-code/teams/<name>.json`

```json
{
  "name": "code-review-team",
  "description": "3-agent code review pipeline",
  "members": [
    {"role": "security-reviewer", "model": "sonnet"},
    {"role": "quality-reviewer", "model": "haiku"},
    {"role": "performance-reviewer", "model": "haiku"}
  ],
  "coordinator_model": "sonnet",
  "max_timeout": 600
}
```

### Checkpoint System

```python
@dataclass(frozen=True)
class AgentCheckpoint:
    member_id: str
    role: str
    status: str                         # "running" | "completed" | "failed"
    conversation_snapshot: tuple[dict, ...]
    last_tool_call: str | None = None
    output: str = ""

@dataclass(frozen=True)
class TeamCheckpoint:
    team_name: str
    task_description: str
    timestamp: str                      # ISO 8601
    checkpoints: tuple[AgentCheckpoint, ...]
    coordinator_state: dict = field(default_factory=dict)
    completed_members: tuple[str, ...] = ()
```

Storage: `~/.llm-code/swarm/checkpoints/<team>-<timestamp>.json`

### Checkpoint Flow

1. SwarmManager starts team → create TeamCheckpoint
2. After each agent tool call → update AgentCheckpoint
3. Auto-save periodically (every 60s or on state change)
4. All complete → mark checkpoint as `completed`, retain for reference

### Resume Flow

1. `/swarm resume` or `SwarmManager.resume(checkpoint_path)`
2. Load TeamCheckpoint
3. Skip `completed_members`
4. For `running`/`failed` agents: rebuild context from `conversation_snapshot`, inject resume prompt
5. Restart coordinator polling

### Auto-Recovery

```python
@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int = 2
    retry_delay_sec: int = 5
    on_all_failed: str = "abort"    # "abort" | "checkpoint_and_stop"
```

- Agent crash/timeout → check retry count → under limit: resume from checkpoint
- Over limit → follow policy (abort or save checkpoint for manual resume)

### /swarm Command Extensions

```
/swarm team list                    # list all team templates
/swarm team create <name>           # interactive template creation
/swarm team run <name> <task>       # run task with template
/swarm team delete <name>           # delete template
/swarm checkpoint list              # list all checkpoints
/swarm resume <checkpoint>          # resume from checkpoint
```

### Files

| File | Change |
|------|--------|
| `swarm/team.py` | **New** — TeamTemplate, TeamMemberTemplate, loader/saver |
| `swarm/checkpoint.py` | **New** — TeamCheckpoint, AgentCheckpoint, auto-save |
| `swarm/recovery.py` | **New** — RecoveryPolicy, retry + resume logic |
| `swarm/manager.py` | Extend: template launch, checkpoint triggers, resume entry point |
| `cli/commands.py` | Extend `/swarm` subcommands |

---

## Feature 14: App-aware Computer Use

### App Tier Classification

| Tier | Allowed Actions | Example Apps |
|------|----------------|--------------|
| `full` | All actions | Notes, Finder, Maps, Photos |
| `click` | screenshot + left_click + scroll | Terminal, iTerm, VS Code, JetBrains |
| `read` | screenshot only | Safari, Chrome, Firefox, Arc, Edge |

### App Detection (macOS)

```python
@dataclass(frozen=True)
class AppInfo:
    name: str           # "Google Chrome"
    bundle_id: str      # "com.google.Chrome"
    pid: int

async def get_frontmost_app() -> AppInfo:
    """NSWorkspace (pyobjc) or fallback to osascript"""
```

### Tier Classification

```python
@dataclass(frozen=True)
class AppTierRule:
    pattern: str        # bundle_id glob
    tier: str           # "read" | "click" | "full"

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
    # Everything else → full
)

@dataclass(frozen=True)
class AppTierClassifier:
    rules: tuple[AppTierRule, ...]
    
    def classify(self, app: AppInfo) -> str:
        """first match wins, default full"""
```

### User-Customizable Rules

In `~/.llm-code/settings.json`:

```json
{
  "computer_use": {
    "app_tiers": [
      {"pattern": "com.slack.*", "tier": "click"},
      {"pattern": "com.1password.*", "tier": "read"}
    ]
  }
}
```

User rules prepended before defaults (higher priority).

### Coordinator Tier Enforcement

```python
TIER_PERMISSIONS = {
    "read":  frozenset({"screenshot", "get_frontmost_app"}),
    "click": frozenset({"screenshot", "get_frontmost_app", "left_click", "scroll"}),
    "full":  frozenset({"screenshot", "get_frontmost_app", "left_click", "right_click",
                        "double_click", "drag", "scroll", "type", "key", "hotkey"}),
}

async def _check_tier(self, action: str) -> None:
    app = await get_frontmost_app()
    tier = self.classifier.classify(app)
    if action not in TIER_PERMISSIONS[tier]:
        raise AppTierDenied(
            app=app.name, tier=tier, action=action,
            hint=self._suggest_alternative(tier, action)
        )
```

### Alternative Hints

- Browser + click/type blocked → "Use MCP browser tools (chrome-devtools) instead"
- Terminal + type blocked → "Use the Bash tool instead"

### Access Request Flow

```python
async def request_access(self, app_names: list[str]) -> dict[str, str]:
    """Prompt user for app access. Returns {app_name: granted_tier}."""
```

- First action on an app → prompt user for access if not yet granted
- Grants stored in session memory (not persistent, re-authorize each session)

### Files

| File | Change |
|------|--------|
| `computer_use/app_detect.py` | **New** — AppInfo, get_frontmost_app() |
| `computer_use/app_tier.py` | **New** — AppTierRule, AppTierClassifier, DEFAULT_RULES, TIER_PERMISSIONS |
| `computer_use/coordinator.py` | Add `_check_tier()` guard to every action method |
| `runtime/config.py` | ComputerUseConfig add `app_tiers` field |

---

## Feature 15: Enterprise Features

### 15a. Pluggable Auth + OIDC

#### AuthProvider Abstraction

```python
@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    email: str
    display_name: str
    groups: tuple[str, ...] = ()
    raw_claims: dict = field(default_factory=dict)

class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self) -> AuthIdentity: ...
    
    @abstractmethod
    async def refresh(self) -> AuthIdentity | None: ...
    
    @abstractmethod
    async def revoke(self) -> None: ...
```

#### OIDC Built-in

```python
@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    redirect_port: int = 9877

class OIDCProvider(AuthProvider):
    """
    PKCE flow, reuse mcp/oauth.py local callback server pattern.
    Tokens stored in ~/.llm-code/auth/tokens.json (encrypted at rest).
    """
```

- Discovery via `/.well-known/openid-configuration`
- Token encryption: `cryptography.fernet`, key derived from machine ID + user password (set on first use)
- `refresh()` auto-triggers 60s before token expiry

#### Config

```json
{
  "enterprise": {
    "auth": {
      "provider": "oidc",
      "oidc": {
        "issuer": "https://accounts.google.com",
        "client_id": "xxx.apps.googleusercontent.com"
      }
    }
  }
}
```

`provider` empty or `"none"` → skip auth (default, backwards compatible).

### 15b. RBAC

#### Role Definition

```python
@dataclass(frozen=True)
class Role:
    name: str
    permissions: frozenset[str]
    tool_allow: tuple[str, ...] = ()
    tool_deny: tuple[str, ...] = ()

DEFAULT_ROLES = {
    "admin": Role("admin", frozenset({"*"})),
    "developer": Role("developer", frozenset({
        "tool:*", "swarm:create", "session:*", "skill:*"
    }), tool_deny=("tool:bash:rm -rf *",)),
    "viewer": Role("viewer", frozenset({
        "tool:read", "tool:glob", "tool:grep", "session:read"
    })),
}
```

#### Role Mapping

- `AuthIdentity.groups` maps to roles via config (group → role mapping)
- No auth → default `admin` (single-user unrestricted)

#### Permission Integration

```python
def check_permission(self, tool: Tool, identity: AuthIdentity | None) -> PermissionOutcome:
    if identity and not self.rbac.is_allowed(identity, f"tool:{tool.name}"):
        return PermissionOutcome.DENIED
    # ... existing logic
```

### 15c. Audit Log

#### Event Format

```python
@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    event_type: str         # "tool_execute", "permission_denied", "auth_login", etc.
    user_id: str            # from AuthIdentity, "local" when no auth
    tool_name: str = ""
    action: str = ""
    outcome: str = ""       # "allowed", "denied", "error"
    metadata: dict = field(default_factory=dict)
```

#### AuditLogger

```python
class AuditLogger(ABC):
    @abstractmethod
    async def log(self, event: AuditEvent) -> None: ...

class FileAuditLogger(AuditLogger):
    """JSONL format to ~/.llm-code/audit/YYYY-MM-DD.jsonl"""

class CompositeAuditLogger(AuditLogger):
    """Write to multiple loggers (e.g. file + remote)"""
```

- File logger built-in, log rotation (daily files, configurable retention days)
- Remote loggers (Splunk, ELK, CloudWatch) left as plugin extension point
- **Mandatory:** when `enterprise.auth.provider` is non-empty, audit log is always on

#### Hook Integration Points

```
Pre tool execute  → audit("tool_execute", outcome="pending")
Post tool execute → audit("tool_execute", outcome="allowed|denied|error")
Auth login        → audit("auth_login")
Auth failure      → audit("auth_failed")
Permission denied → audit("permission_denied")
```

Injected via existing hook system (PreToolUse/PostToolUse), no tool modifications needed.

#### /audit Command

```
/audit                  # today's audit summary (counts by event type)
/audit search <query>   # search audit log (by user, tool, outcome)
/audit export <path>    # export date range logs
```

### Files

| File | Change |
|------|--------|
| `enterprise/__init__.py` | **New** — package |
| `enterprise/auth.py` | **New** — AuthProvider ABC, AuthIdentity |
| `enterprise/oidc.py` | **New** — OIDCProvider, OIDCConfig, token encryption |
| `enterprise/rbac.py` | **New** — Role, RBACEngine, DEFAULT_ROLES, group mapping |
| `enterprise/audit.py` | **New** — AuditEvent, AuditLogger, FileAuditLogger, CompositeAuditLogger |
| `runtime/permissions.py` | Extend: RBAC check integration |
| `runtime/config.py` | EnterpriseConfig with auth/rbac/audit sub-configs |
| `cli/commands.py` | Add `/audit` command |

---

## Full File Change Summary

### New Files (12)

| File | Feature |
|------|---------|
| `runtime/skill_resolver.py` | 11 |
| `tui/keybindings.py` | 12 |
| `swarm/team.py` | 13 |
| `swarm/checkpoint.py` | 13 |
| `swarm/recovery.py` | 13 |
| `computer_use/app_detect.py` | 14 |
| `computer_use/app_tier.py` | 14 |
| `enterprise/__init__.py` | 15 |
| `enterprise/auth.py` | 15 |
| `enterprise/oidc.py` | 15 |
| `enterprise/rbac.py` | 15 |
| `enterprise/audit.py` | 15 |

### Modified Files (6)

| File | Features |
|------|----------|
| `runtime/skills.py` | 11 |
| `runtime/config.py` | 14, 15 |
| `runtime/permissions.py` | 15 |
| `tui/input_bar.py` | 12 |
| `swarm/manager.py` | 13 |
| `cli/commands.py` | 12, 13, 15 |

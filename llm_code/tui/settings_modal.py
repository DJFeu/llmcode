"""Read-only settings panel — sectioned view of runtime state.

Sections: Status, Config, Usage, Keybindings, Skills.
TODO: write-back support in a future iteration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsSection:
    title: str
    fields: list[tuple[str, str]]


def build_settings_sections(runtime: object) -> list[SettingsSection]:
    """Construct read-only settings sections from a runtime/app object.

    Defensive against missing attributes — never raises.
    """
    sections: list[SettingsSection] = []

    # Status
    status: list[tuple[str, str]] = []
    status.append(("Model", _safe(runtime, "model", "—")))
    status.append(("Permission mode", _safe(runtime, "permission_mode", "—")))
    status.append(("Plan mode", str(_safe(runtime, "plan_mode", False))))
    sections.append(SettingsSection("Status", status))

    # Config
    cfg = _safe(runtime, "config", None)
    config_fields: list[tuple[str, str]] = []
    if cfg is not None:
        for key in ("provider", "model", "max_tokens", "temperature", "thinking_enabled"):
            if hasattr(cfg, key):
                config_fields.append((key, str(getattr(cfg, key))))
    if not config_fields:
        config_fields.append(("(empty)", ""))
    sections.append(SettingsSection("Config", config_fields))

    # Usage
    ct = _safe(runtime, "cost_tracker", None)
    usage: list[tuple[str, str]] = []
    if ct is not None:
        usage.append(("Input tokens", f"{getattr(ct, 'total_input_tokens', 0):,}"))
        usage.append(("Output tokens", f"{getattr(ct, 'total_output_tokens', 0):,}"))
        usage.append(("Total cost", f"${getattr(ct, 'total_cost_usd', 0.0):.4f}"))
    else:
        usage.append(("(no tracker)", ""))
    sections.append(SettingsSection("Usage", usage))

    # Keybindings
    kb = _safe(runtime, "keybindings", None)
    kb_fields: list[tuple[str, str]] = []
    if kb is not None and hasattr(kb, "get_all_bindings"):
        try:
            for action, key in sorted(kb.get_all_bindings().items()):
                kb_fields.append((action, key))
        except Exception:
            pass
    if not kb_fields:
        kb_fields.append(("(none)", ""))
    sections.append(SettingsSection("Keybindings", kb_fields))

    # Skills
    skills = _safe(runtime, "active_skills", None)
    skill_fields: list[tuple[str, str]] = []
    if isinstance(skills, (list, tuple, set)):
        for s in sorted(skills):
            skill_fields.append((str(s), "enabled"))
    if not skill_fields:
        skill_fields.append(("(none)", ""))
    sections.append(SettingsSection("Skills", skill_fields))

    return sections


def _safe(obj: object, attr: str, default):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def render_sections_text(sections: list[SettingsSection]) -> str:
    """Render sections as a plain text block (used by tests + simple display)."""
    out: list[str] = []
    for sec in sections:
        out.append(f"── {sec.title} ──")
        for k, v in sec.fields:
            out.append(f"  {k:24} {v}")
        out.append("")
    return "\n".join(out)

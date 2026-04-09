"""Settings panel — sectioned view of runtime state with write-back.

Sections: Status, Config, Usage, Keybindings, Skills.
Editable fields: temperature, max_tokens, model (via apply_setting).
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


_EDITABLE_FIELDS = frozenset({"temperature", "max_tokens", "model"})


def editable_fields() -> frozenset[str]:
    """Return the set of config field names that support write-back."""
    return _EDITABLE_FIELDS


def apply_setting(config: object, key: str, value: str) -> object:
    """Return a new config with *key* set to *value*.

    Only ``_EDITABLE_FIELDS`` are accepted. Raises ``ValueError`` for
    unknown keys or invalid values. Returns a new frozen config via
    ``dataclasses.replace``.
    """
    import dataclasses as _dc

    if key not in _EDITABLE_FIELDS:
        raise ValueError(f"Field '{key}' is not editable. Editable: {sorted(_EDITABLE_FIELDS)}")

    if key == "temperature":
        typed_value = float(value)
        if not (0.0 <= typed_value <= 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")
        return _dc.replace(config, temperature=typed_value)  # type: ignore[misc]
    if key == "max_tokens":
        typed_int = int(value)
        if typed_int < 1:
            raise ValueError("max_tokens must be >= 1")
        return _dc.replace(config, max_tokens=typed_int)  # type: ignore[misc]
    if key == "model":
        return _dc.replace(config, model=value.strip())  # type: ignore[misc]

    raise ValueError(f"Unhandled field: {key}")


def render_sections_text(sections: list[SettingsSection]) -> str:
    """Render sections as a plain text block (used by tests + simple display)."""
    out: list[str] = []
    for sec in sections:
        out.append(f"── {sec.title} ──")
        for k, v in sec.fields:
            out.append(f"  {k:24} {v}")
        out.append("")
    return "\n".join(out)

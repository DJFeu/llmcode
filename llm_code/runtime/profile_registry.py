"""Profile registry — model_id → ModelProfile resolution (v13 Phase A).

This module is the single source of truth for "which profile does
llmcode use for this model id?" It is complementary to — not a
replacement for — ``llm_code.runtime.model_profile.ProfileRegistry``
(the older per-model lookup table keyed by model name).

v13 introduces a *second* resolution path driven by ``ModelProfile.
prompt_match`` tuples (substring tokens the profile author declares
in the ``[prompt]`` TOML section). The new resolver walks a flat
ordered list of profiles and picks the first whose ``prompt_match``
tuple contains a substring of ``model_id.lower()``.

Design choices (match the plan file):

* **No eager import-time side effect.** The module-level
  ``_PROFILES`` list starts empty. Callers decide when to populate
  it — via ``register_profile()`` (programmatic) or
  ``_load_builtin_profiles(path)`` (TOML directory sweep).
* **Lazy built-in load.** ``_ensure_builtin_profiles_loaded()``
  runs the directory sweep once per process. Called from
  :class:`~llm_code.runtime.prompt.SystemPromptBuilder` and from the
  :func:`~llm_code.runtime.prompt.select_intro_prompt` deprecation
  shim so downstream callers never have to remember to populate
  the registry.
* **User profiles first, built-ins last.** Registration order is
  preserved. A caller that wants a user override to win simply
  registers it before ``_ensure_builtin_profiles_loaded()`` runs.

Phase C (v2.3.0) deleted the legacy ``_legacy_select_intro_prompt``
if-ladder. Every built-in profile under ``examples/model_profiles/``
now declares a ``[prompt]`` section, so
``resolve_profile_for_model`` plus ``load_intro_prompt`` is the only
route from a ``model_id`` to its tuned intro prompt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from llm_code.runtime.model_profile import (
    ModelProfile,
    _load_toml,
    _profile_from_dict,
)

_logger = logging.getLogger(__name__)

# Global, ordered registry. Earlier entries win on match.
_PROFILES: list[ModelProfile] = []

# Returned when ``model_id`` does not match any registered profile
# or when the caller passes an empty string. The default is a plain
# ``ModelProfile()`` so every field takes its dataclass default —
# i.e. ``prompt_template == ""`` which ``load_intro_prompt`` treats
# as "use the engine/prompts/models/default.j2 template".
_DEFAULT_PROFILE: ModelProfile = ModelProfile()

# Guards ``_ensure_builtin_profiles_loaded`` so the directory sweep
# runs at most once per process.
_builtin_loaded: bool = False


class ProfileMatchCollision(RuntimeError):
    """Raised when ``register_profile`` sees a ``prompt_match`` token
    that is already owned by a previously registered profile.

    Two profiles claiming the same substring token (e.g. both claim
    ``"glm"``) would make ``resolve_profile_for_model`` pick the first
    one silently, hiding the second. Raising at registration time
    surfaces the conflict immediately.
    """


# ── Registration ──────────────────────────────────────────────────────


def register_profile(
    profile: ModelProfile,
    *,
    check_collision: bool = True,
) -> None:
    """Append ``profile`` to the registry.

    Args:
        profile: The ``ModelProfile`` to register. Its ``prompt_match``
            tuple decides when ``resolve_profile_for_model`` picks it.
        check_collision: When True (the default), raise
            :class:`ProfileMatchCollision` if any token in
            ``profile.prompt_match`` is already owned by a previously
            registered profile. Set to False for bulk/batch loaders
            that want to tolerate duplicates (e.g. dev reloads).

    Raises:
        ProfileMatchCollision: Only when ``check_collision`` is True
            and a duplicate token is detected.
    """
    if check_collision and profile.prompt_match:
        seen: dict[str, ModelProfile] = {}
        for registered in _PROFILES:
            for token in registered.prompt_match:
                seen[token] = registered
        for token in profile.prompt_match:
            if token in seen:
                existing = seen[token]
                raise ProfileMatchCollision(
                    f"match token {token!r} already owned by profile "
                    f"{existing.name!r}; profile {profile.name!r} "
                    "would collide"
                )
    _PROFILES.append(profile)


# ── Resolution ────────────────────────────────────────────────────────


def resolve_profile_for_model(model_id: str) -> ModelProfile:
    """Return the first registered profile whose ``prompt_match`` tuple
    contains a substring of ``model_id.lower()``.

    Falls back to ``_DEFAULT_PROFILE`` when:

    * ``model_id`` is empty
    * No registered profile claims any substring of ``model_id``
    * The registry is empty (e.g. in a test fixture that reset it)

    The match is substring-based and case-insensitive on ``model_id``;
    tokens themselves are expected to already be lowercase (the TOML
    loader enforces this).
    """
    if not model_id:
        return _DEFAULT_PROFILE
    m = model_id.lower()
    for profile in _PROFILES:
        for token in profile.prompt_match:
            if token and token in m:
                return profile
    return _DEFAULT_PROFILE


# ── Bulk loading helpers ──────────────────────────────────────────────


def _load_builtin_profiles(path: Path) -> int:
    """Walk ``path`` and register every ``*.toml`` profile found.

    Returns the number of profiles successfully registered. Failures
    (malformed TOML, collisions, filesystem errors) are logged at
    debug level and skipped so a single bad file cannot crash the
    caller.

    This is the explicit, manual loader. See
    :func:`_ensure_builtin_profiles_loaded` for the lazy variant
    that runs at most once per process.
    """
    if not path.is_dir():
        return 0

    count = 0
    for toml_path in sorted(path.glob("*.toml")):
        try:
            data: dict[str, Any] = _load_toml(toml_path)
            profile = _profile_from_dict(data)
            register_profile(profile)
            count += 1
        except ProfileMatchCollision as exc:
            _logger.debug(
                "profile registry: skipping %s — %s", toml_path, exc
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug(
                "profile registry: failed to load %s — %s", toml_path, exc
            )
    return count


def _ensure_builtin_profiles_loaded() -> None:
    """Load the packaged ``examples/model_profiles/*.toml`` once.

    Idempotent: the second and subsequent calls are no-ops. Tests
    that want a clean registry should call
    :func:`_reset_registry_for_tests` first.

    The search path is computed from this module's location so the
    function works both from an editable install (source tree) and,
    for Phase A, whenever ``examples/model_profiles`` is present in
    the repo. Packaged wheels without ``examples/`` are a no-op — a
    future phase packages profiles inside the wheel.
    """
    global _builtin_loaded
    if _builtin_loaded:
        return
    # Flip the flag early so a filesystem error on the sweep doesn't
    # leave the guard stuck open and retry on every subsequent call.
    _builtin_loaded = True
    # ``llm_code/runtime/profile_registry.py`` → parents[2] = repo root.
    builtin_dir = (
        Path(__file__).resolve().parents[2] / "examples" / "model_profiles"
    )
    _load_builtin_profiles(builtin_dir)


# ── Test helpers ──────────────────────────────────────────────────────


def _reset_registry_for_tests() -> None:
    """Clear the registry and reset the lazy-load guard.

    Intended for unit test fixtures. Not part of the public API —
    prefer building isolated ``ProfileRegistry`` instances from
    ``llm_code.runtime.model_profile`` for production code.
    """
    global _builtin_loaded
    _PROFILES.clear()
    _builtin_loaded = False

"""Skill lazy-loader helper (M12).

Claude Code lazy-loads slash commands / skills so cold start doesn't
block on every registered skill's parser. This decorator gives
llm-code the same primitive — wrap any skill factory and the first
call builds + caches; subsequent calls reuse.
"""
from __future__ import annotations

from typing import Any, Callable


def lazy_skill(factory: Callable[[], Any]) -> Callable[[], Any]:
    sentinel = object()
    state: dict[str, Any] = {"value": sentinel}

    def wrapped() -> Any:
        if state["value"] is sentinel:
            state["value"] = factory()
        return state["value"]

    def reset() -> None:
        state["value"] = sentinel

    wrapped.reset = reset  # type: ignore[attr-defined]
    return wrapped

"""Footer hint row renderer (M15 Task A6).

Renders the 1-row hint strip that sits below the input area,
showing context-dependent keybinding hints ("Ctrl+G voice",
"/ commands", "↑ history", "Ctrl+D quit"). Colors come from
``palette.hint_fg``.
"""
from __future__ import annotations

from typing import Callable, List, Tuple

from llm_code.view.repl import style

__all__ = ["FooterHint"]


class FooterHint:
    """Renders the hint row below the input area.

    The hint text is static by default but the class is open for
    future context-aware variants (e.g. "Ctrl+Space accept" when
    a completion menu is visible). Each hint tuple is
    ``(label, action)`` and is rendered as ``label action  ·``.
    """

    def __init__(
        self,
        *,
        hint_provider: Callable[[], List[Tuple[str, str]]] | None = None,
    ) -> None:
        self._hint_provider = hint_provider or self._default_hints

    @staticmethod
    def _default_hints() -> List[Tuple[str, str]]:
        return [
            ("Ctrl+G", "voice"),
            ("/", "commands"),
            ("↑", "history"),
            ("Ctrl+O", "expand"),
            ("Ctrl+D", "quit"),
        ]

    def render(self) -> List[Tuple[str, str]]:
        """Return a PT ``FormattedText``-compatible list."""
        hints = self._hint_provider()
        out: list[tuple[str, str]] = []
        sep = (f"fg:{style.palette.hint_fg}", "  ·  ")
        for i, (key, action) in enumerate(hints):
            if i > 0:
                out.append(sep)
            out.append((f"fg:{style.palette.brand_accent} bold", key))
            out.append((f"fg:{style.palette.hint_fg}", f" {action}"))
        return out

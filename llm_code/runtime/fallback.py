"""Wave2-3: declarative model fallback chain.

The conversation runtime originally tracked a single ``fallback: str`` on
``ModelRoutingConfig`` and switched to it after 3 consecutive provider
errors. That covers "primary → cheaper backup" but breaks down when you
want "Claude → OpenAI → local Ollama" — once a second failure happens on
the backup there is nowhere left to go.

``FallbackChain`` replaces that single-shot switch with an ordered list
plus a tiny ``next(current, error_kind)`` API so the caller doesn't need
to know where in the chain it currently is. It is intentionally
stateless: per-model retry counters live on the runtime itself, because
they depend on *consecutive* error tracking that the chain has no
opinion about.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Sequence

if TYPE_CHECKING:
    from llm_code.runtime.config import ModelRoutingConfig


#: Error kinds the chain is willing to escalate on. Everything else is
#: treated as terminal (e.g. auth failures, malformed requests).
_RETRYABLE_KINDS = frozenset({"retryable", "stream_error", "timeout", "rate_limit"})


@dataclass(frozen=True)
class FallbackChain:
    """Ordered list of fallback models.

    >>> chain = FallbackChain(["sonnet", "haiku", "gpt-4o"])
    >>> chain.next("sonnet")
    'haiku'
    >>> chain.next("haiku")
    'gpt-4o'
    >>> chain.next("gpt-4o") is None
    True
    """

    models: tuple[str, ...]

    def __init__(self, models: Sequence[str]) -> None:
        # Preserve order but drop falsy entries (from YAML `fallbacks: [""]` etc.)
        cleaned = tuple(m for m in models if m)
        object.__setattr__(self, "models", cleaned)

    def __iter__(self) -> Iterator[str]:
        return iter(self.models)

    def __bool__(self) -> bool:
        return bool(self.models)

    def next(
        self,
        current: str,
        *,
        error_kind: str = "retryable",
    ) -> str | None:
        """Return the next fallback model, or ``None`` when exhausted.

        * ``current`` is the model that just failed. If it is not part
          of the chain (e.g. the primary model is kept out of its own
          fallback list), the first entry is returned instead.
        * ``error_kind`` lets the caller opt out — any value outside
          the retryable set short-circuits to ``None`` so non-retryable
          errors (auth, model-not-found, 413) don't chew through
          fallback budget.
        """
        if error_kind not in _RETRYABLE_KINDS:
            return None
        if not self.models:
            return None
        try:
            idx = self.models.index(current)
        except ValueError:
            return self.models[0]
        nxt = idx + 1
        if nxt >= len(self.models):
            return None
        return self.models[nxt]

    @classmethod
    def from_routing(cls, routing: "ModelRoutingConfig") -> "FallbackChain":
        """Build a chain from a ``ModelRoutingConfig``.

        Precedence rules:
        1. ``fallbacks: tuple[str, ...]`` when non-empty — the new API.
        2. ``fallback: str`` when set — legacy single-shot config,
           promoted to a 1-element chain for backward compatibility.
        3. Otherwise an empty chain that always returns ``None``.
        """
        fallbacks = getattr(routing, "fallbacks", ()) or ()
        if fallbacks:
            return cls(tuple(fallbacks))
        legacy = getattr(routing, "fallback", "") or ""
        if legacy:
            return cls((legacy,))
        return cls(())

"""Token budget tracking for agentic conversation turns."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenBudget:
    target: int
    consumed: int = 0

    def add(self, output_tokens: int) -> None:
        self.consumed += output_tokens

    def remaining(self) -> int:
        return max(0, self.target - self.consumed)

    def should_nudge(self) -> bool:
        return self.consumed < self.target

    def is_exhausted(self) -> bool:
        return self.consumed >= self.target

    def nudge_message(self) -> str:
        rem = self.remaining()
        return f"[Token budget: {rem:,} tokens remaining out of {self.target:,}. Continue working toward the goal.]"

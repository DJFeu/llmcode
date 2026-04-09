"""Token cost tracking with model pricing — user-customizable via config."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Built-in fallback pricing per 1M tokens [input, output]
BUILTIN_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o3": (2.00, 8.00),
    "o4-mini": (0.50, 2.00),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # xAI
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
}


class BudgetExceededError(Exception):
    """Raised when the accumulated cost exceeds the configured budget."""

    def __init__(self, spent: float, budget: float) -> None:
        self.spent = spent
        self.budget = budget
        super().__init__(f"Budget limit (${budget:.2f}) exceeded (spent ${spent:.4f})")


@dataclass
class CostTracker:
    model: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    custom_pricing: dict | None = None  # from config.json "pricing"
    max_budget_usd: float | None = None
    rate_limit_info: dict | None = None  # {used, limit, reset_at: epoch seconds} or None
    # Wave2-2: models we've already warned about so we don't spam the log
    # on every single add_usage() call when running with a custom model.
    _warned_unknown_models: set[str] = field(default_factory=set)

    def add_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """Record token usage and return the cost of this request in USD."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        in_price, out_price = self._get_pricing()
        request_cost = (
            input_tokens * in_price
            + output_tokens * out_price
            + cache_read_tokens * in_price * 0.10
            + cache_creation_tokens * in_price * 1.25
        ) / 1_000_000
        self.total_cost_usd += request_cost
        return request_cost

    def is_budget_exceeded(self) -> bool:
        """Return True if a budget is set and has been exceeded."""
        if self.max_budget_usd is None:
            return False
        return self.total_cost_usd > self.max_budget_usd

    def remaining_budget(self) -> float | None:
        """Return remaining budget in USD, or None if no budget is set."""
        if self.max_budget_usd is None:
            return None
        return max(0.0, self.max_budget_usd - self.total_cost_usd)

    def check_budget(self) -> None:
        """Raise BudgetExceededError if the budget has been exceeded."""
        if self.is_budget_exceeded():
            raise BudgetExceededError(
                spent=self.total_cost_usd,
                budget=self.max_budget_usd,  # type: ignore[arg-type]
            )

    def _get_pricing(self) -> tuple[float, float]:
        # 1. User custom pricing (exact match)
        if self.custom_pricing:
            if self.model in self.custom_pricing:
                p = self.custom_pricing[self.model]
                return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)
            # Partial match in custom
            for key, p in self.custom_pricing.items():
                if key != "default" and key in self.model:
                    return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)
            # Custom default
            if "default" in self.custom_pricing:
                p = self.custom_pricing["default"]
                return (p[0], p[1]) if isinstance(p, list) else (0.0, 0.0)

        # 2. Built-in pricing (exact match)
        if self.model in BUILTIN_PRICING:
            return BUILTIN_PRICING[self.model]

        # 3. Built-in pricing (partial match)
        model_lower = self.model.lower()
        for key, pricing in BUILTIN_PRICING.items():
            if key in model_lower:
                return pricing

        # 4. Unknown model = free. Warn once per model so self-hosted
        # setups (Qwen on GX10 etc.) stay silent but truly-unknown names
        # surface in the log the first time they're seen.
        if self.model and self.model not in self._warned_unknown_models:
            self._warned_unknown_models.add(self.model)
            logger.warning(
                "cost_tracker: no pricing entry for model %r; treating as free. "
                "Add a custom_pricing row in config if this is a paid model.",
                self.model,
            )
        return (0.0, 0.0)

    def to_dict(self) -> dict:
        """Serialize accumulated cost state for session persistence."""
        return {
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
        }

    def restore_from_dict(self, data: dict) -> None:
        """Restore accumulated cost state from a persisted dict.

        Adds the persisted values ON TOP of the current state so a
        resumed session's cost is cumulative with any usage that
        happened before the restore call (e.g. the resume itself
        may have already incurred a small request).
        """
        self.total_input_tokens += data.get("total_input_tokens", 0)
        self.total_output_tokens += data.get("total_output_tokens", 0)
        self.total_cost_usd += data.get("total_cost_usd", 0.0)

    def format_cost(self) -> str:
        lines = [f"Tokens — in: {self.total_input_tokens:,}  out: {self.total_output_tokens:,}"]
        in_price, out_price = self._get_pricing()
        if self.total_cost_usd > 0.0001:
            lines.append(f"  Cost: ${self.total_cost_usd:.4f}")
            lines.append(f"  Rate: ${in_price}/1M in · ${out_price}/1M out")
        elif in_price == 0 and out_price == 0:
            lines.append("  Cost: $0 (free / local model)")
        else:
            lines.append(f"  Cost: ${self.total_cost_usd:.6f}")
        return "  ".join(lines)

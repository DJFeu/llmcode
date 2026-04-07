"""Orchestrator hook with category-based delegation and retry-with-different-persona.

Minimal port of oh-my-opencode's atlas hook (~773 lines TS) — kept under 200 lines
by dropping deep coordinator coupling and event-loop machinery, while preserving
the core ideas:

- Classify an incoming task by keyword category (refactor / debug / explain / search / build / test)
- Map category -> preferred persona name (Wave 1 personas)
- On failure, retry with a *different* persona, accumulating prior failure context
- Cap retries at 3
- Per-attempt log entry callable for the runtime to render
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from llm_code.swarm.personas import BUILTIN_PERSONAS, AgentPersona

# Category keyword map
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "refactor": ("refactor", "rename", "extract", "clean up", "simplify"),
    "debug": ("debug", "fix", "broken", "crash", "stack trace", "exception"),
    "explain": ("explain", "what is", "how does", "why", "describe", "walk through"),
    "search": ("search", "find", "locate", "grep", "where is"),
    "build": ("build", "implement", "scaffold", "create", "add feature", "ship"),
    "test": ("test", "pytest", "coverage", "unit test"),
}

# Category -> ordered list of preferred persona names (first = primary, rest = retry fallbacks).
CATEGORY_PERSONAS: dict[str, tuple[str, ...]] = {
    "refactor": ("sisyphus", "sisyphus-junior", "oracle"),
    "debug": ("sisyphus", "oracle", "explore"),
    "explain": ("librarian", "oracle", "explore"),
    "search": ("explore", "librarian", "sisyphus-junior"),
    "build": ("sisyphus", "atlas", "sisyphus-junior"),
    "test": ("sisyphus-junior", "sisyphus", "metis"),
    "unknown": ("sisyphus", "oracle", "librarian"),
}

MAX_RETRIES = 3


def categorize(task: str) -> str:
    """Return the best-matching category for *task* (lowercase keyword scan)."""
    t = (task or "").lower()
    best_category = "unknown"
    best_score = 0
    for category, words in CATEGORY_KEYWORDS.items():
        score = sum(1 for w in words if w in t)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def select_persona(category: str, attempted: tuple[str, ...] = ()) -> Optional[AgentPersona]:
    """Pick the next persona for *category* that hasn't been *attempted* yet."""
    candidates = CATEGORY_PERSONAS.get(category, CATEGORY_PERSONAS["unknown"])
    for name in candidates:
        if name in attempted:
            continue
        persona = BUILTIN_PERSONAS.get(name)
        if persona is not None:
            return persona
    return None


@dataclass
class AttemptLog:
    attempt: int
    persona: str
    category: str
    success: bool
    error: str = ""


@dataclass
class OrchestrationResult:
    success: bool
    final_output: str = ""
    attempts: list[AttemptLog] = field(default_factory=list)


# Persona executor signature: (persona, task) -> (success, output_or_error)
PersonaExecutor = Callable[[AgentPersona, str], tuple[bool, str]]
AsyncPersonaExecutor = Callable[[AgentPersona, str], Awaitable[tuple[bool, str]]]


class OrchestratorHook:
    """Category-routed delegation with retry-on-failure.

    Wraps an arbitrary *executor* callable so the same logic can be used
    over a Coordinator, a stub for tests, or a future swarm backend.
    """

    def __init__(
        self,
        executor: PersonaExecutor,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._executor = executor
        self._max_retries = max(1, max_retries)

    def orchestrate(self, task: str) -> OrchestrationResult:
        category = categorize(task)
        attempted: list[str] = []
        attempts: list[AttemptLog] = []
        accumulated_context = task

        for attempt_idx in range(1, self._max_retries + 1):
            persona = select_persona(category, tuple(attempted))
            if persona is None:
                attempts.append(
                    AttemptLog(
                        attempt=attempt_idx,
                        persona="<none>",
                        category=category,
                        success=False,
                        error="no remaining personas for category",
                    )
                )
                break
            attempted.append(persona.name)
            success, output = self._executor(persona, accumulated_context)
            attempts.append(
                AttemptLog(
                    attempt=attempt_idx,
                    persona=persona.name,
                    category=category,
                    success=success,
                    error="" if success else output[:300],
                )
            )
            if success:
                return OrchestrationResult(
                    success=True, final_output=output, attempts=attempts
                )
            # Append failure context for next retry
            accumulated_context = (
                f"{accumulated_context}\n\n"
                f"[previous attempt by {persona.name} failed: {output[:300]}]"
            )

        return OrchestrationResult(success=False, final_output="", attempts=attempts)

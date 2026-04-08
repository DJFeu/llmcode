"""Built-in agent personas ported from oh-my-opencode.

Each persona is a frozen dataclass describing system prompt, model hint,
temperature, and tool restrictions for a specialized swarm worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentPersona:
    """Specialized agent configuration for a swarm member.

    Attributes:
        name: Persona identifier (lowercase, hyphenated).
        description: Short summary of the persona's role.
        model_hint: Hint for model resolution: "thinking" | "fast" | "default".
        temperature: Sampling temperature.
        allowed_tools: Whitelist of tool names. Empty tuple = no whitelist.
        denied_tools: Blocklist of tool names that must be denied.
        system_prompt: Full system prompt prepended to the worker session.
    """

    name: str
    description: str
    system_prompt: str
    model_hint: str = "default"
    temperature: float = 0.2
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    denied_tools: tuple[str, ...] = field(default_factory=tuple)
    # Names of MCP servers (from RuntimeConfig.mcp.on_demand) this persona
    # requires. Spawned lazily before the persona runs, torn down after.
    mcp_servers: tuple[str, ...] = field(default_factory=tuple)


from llm_code.swarm.personas.sisyphus import SISYPHUS  # noqa: E402
from llm_code.swarm.personas.sisyphus_junior import SISYPHUS_JUNIOR  # noqa: E402
from llm_code.swarm.personas.oracle import ORACLE  # noqa: E402
from llm_code.swarm.personas.librarian import LIBRARIAN  # noqa: E402
from llm_code.swarm.personas.atlas import ATLAS  # noqa: E402
from llm_code.swarm.personas.explore import EXPLORE  # noqa: E402
from llm_code.swarm.personas.metis import METIS  # noqa: E402
from llm_code.swarm.personas.momus import MOMUS  # noqa: E402
from llm_code.swarm.personas.multimodal_looker import MULTIMODAL_LOOKER  # noqa: E402
from llm_code.swarm.personas.web_researcher import WEB_RESEARCHER  # noqa: E402

BUILTIN_PERSONAS: dict[str, AgentPersona] = {
    p.name: p
    for p in (
        SISYPHUS,
        SISYPHUS_JUNIOR,
        ORACLE,
        LIBRARIAN,
        ATLAS,
        EXPLORE,
        METIS,
        MOMUS,
        MULTIMODAL_LOOKER,
        WEB_RESEARCHER,
    )
}

__all__ = ["AgentPersona", "BUILTIN_PERSONAS"]

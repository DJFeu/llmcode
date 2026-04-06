"""Harness configuration types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessControl:
    """A single harness control (guide or sensor)."""

    name: str
    category: str  # "guide" | "sensor"
    kind: str  # "computational" | "inferential"
    enabled: bool = True
    trigger: str = "post_tool"  # "pre_tool" | "post_tool" | "pre_turn" | "post_turn" | "on_demand"


@dataclass(frozen=True)
class HarnessFinding:
    """A finding reported by a sensor after tool execution."""

    sensor: str
    message: str
    file_path: str = ""
    severity: str = "info"  # "error" | "warning" | "info"


@dataclass(frozen=True)
class HarnessConfig:
    """Configuration for the Harness Engine."""

    template: str = "auto"
    controls: tuple[HarnessControl, ...] = ()

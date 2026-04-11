"""Feature-specific frozen dataclasses extracted from config.py."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DreamConfig:
    enabled: bool = True
    min_turns: int = 3


@dataclass(frozen=True)
class KnowledgeConfig:
    enabled: bool = True
    compile_on_exit: bool = True
    max_context_tokens: int = 3000
    compile_model: str = ""


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    backend: str = "whisper"  # "local" | "whisper" | "google" | "anthropic"
    whisper_url: str = "http://localhost:8000/v1/audio/transcriptions"
    google_language_code: str = ""
    anthropic_ws_url: str = "wss://api.anthropic.com"
    language: str = "en"
    # Toggle hotkey bound at the InputBar. Default is Ctrl+G (ASCII
    # BEL, 0x07) because Ctrl+Space collides with the macOS Input
    # Source switcher on a default system — the OS swallows the key
    # before it reaches the terminal, so the binding looks "broken"
    # to the user. Ctrl+G has no system-level binding on macOS and
    # is rarely used by shells (readline treats it as abort-input,
    # which is harmless inside a TUI). Change to any Textual key
    # name if you want a different binding.
    hotkey: str = "ctrl+g"
    # Local faster-whisper model size when backend == "local".
    # One of: tiny | base | small | medium | large-v3. Larger = slower
    # but more accurate; downloaded lazily into ~/.cache/huggingface/.
    local_model: str = "base"
    # Voice-activity detection: after this many seconds of silence,
    # the recorder flags itself for auto-stop so the TUI can tear the
    # capture down without waiting for an explicit `/voice off`.
    # 0 disables VAD entirely (old behavior — user must stop manually).
    silence_seconds: float = 2.0
    # Peak 16-bit sample amplitude below which a chunk counts as
    # silent. Speech peaks at 10000-20000 on the first syllable;
    # room silence / fan hum / mic self-noise rarely peak above 2000.
    # 3000 leaves a comfortable margin. Raise if VAD triggers too
    # early in noisy environments (you'll see it never fire); lower
    # if VAD refuses to stop in a quiet room.
    silence_threshold: int = 3000


@dataclass(frozen=True)
class ComputerUseConfig:
    enabled: bool = False
    screenshot_delay: float = 0.5
    app_tiers: tuple[dict, ...] = ()  # user-defined tier overrides


@dataclass(frozen=True)
class IDEConfig:
    enabled: bool = False
    port: int = 9876


@dataclass(frozen=True)
class SwarmConfig:
    enabled: bool = False
    backend: str = "auto"       # "auto" | "tmux" | "subprocess" | "worktree"
    max_members: int = 5
    role_models: dict[str, str] = field(default_factory=dict)
    worktree: "WorktreeConfig" = field(default_factory=lambda: WorktreeConfig())
    overlap_threshold: float = 0.6
    synthesis_enabled: bool = True


@dataclass(frozen=True)
class WorktreeConfig:
    on_complete: str = "diff"   # "diff" | "merge" | "branch"
    base_dir: str = ""
    copy_gitignored: tuple[str, ...] = (".env", ".env.local")
    cleanup_on_success: bool = True


@dataclass(frozen=True)
class VCRConfig:
    enabled: bool = False
    auto_record: bool = False


@dataclass(frozen=True)
class HidaConfig:
    enabled: bool = False
    confidence_threshold: float = 0.6
    custom_profiles: tuple[dict, ...] = ()


@dataclass(frozen=True)
class DiminishingReturnsConfig:
    """Auto-stop when model produces diminishing output per continuation."""

    enabled: bool = True
    min_continuations: int = 3   # minimum iterations before checking
    min_delta_tokens: int = 500  # stop if delta below this
    auto_stop_message: str = (
        "\n[Auto-stopped: diminishing returns — iteration {iteration}, {delta} new tokens]"
    )

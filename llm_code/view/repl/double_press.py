"""Double-press confirmation for Ctrl+C / Ctrl+D within a time window."""
from __future__ import annotations

from dataclasses import dataclass, field

DOUBLE_PRESS_WINDOW = 1.5  # seconds


@dataclass
class DoublePressTracker:
    """Tracks last-press timestamps for confirmation keys.

    Usage:
        if tracker.press("ctrl+c", time.monotonic()):
            # confirmed; perform action
        else:
            # show "press again" hint
    """
    window: float = DOUBLE_PRESS_WINDOW
    _last: dict[str, float] = field(default_factory=dict)

    def press(self, key: str, now: float) -> bool:
        """Record a press; return True if it confirms a previous press."""
        last = self._last.get(key)
        if last is not None and (now - last) <= self.window:
            self._last.pop(key, None)
            return True
        self._last[key] = now
        return False

    def reset(self, key: str | None = None) -> None:
        """Clear pending press(es). Called when any other key is pressed."""
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)

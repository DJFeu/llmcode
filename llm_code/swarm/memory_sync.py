"""Shared memory store with file locking for swarm members."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _lock_file(f) -> None:  # type: ignore[no-untyped-def]
    """Acquire an exclusive lock on a file handle (platform-aware)."""
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX)


def _unlock_file(f) -> None:  # type: ignore[no-untyped-def]
    """Release a file lock (platform-aware)."""
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


class SharedMemory:
    """JSON-backed shared key-value store with file locking.

    Multiple swarm members can safely read/write to the same file.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, key: str, value: str) -> None:
        """Set a key-value pair (locked)."""
        data = self._locked_read()
        data[key] = value
        self._locked_write(data)

    def read(self, key: str) -> str | None:
        """Get a value by key, or None if missing."""
        data = self._locked_read()
        return data.get(key)

    def read_all(self) -> dict[str, str]:
        """Return the entire shared memory dict."""
        return self._locked_read()

    def delete(self, key: str) -> None:
        """Remove a key (no-op if missing)."""
        data = self._locked_read()
        data.pop(key, None)
        self._locked_write(data)

    def _locked_read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                _lock_file(f)
                try:
                    content = f.read()
                    return json.loads(content) if content.strip() else {}
                finally:
                    _unlock_file(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _locked_write(self, data: dict[str, str]) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            _lock_file(f)
            try:
                json.dump(data, f, indent=2)
            finally:
                _unlock_file(f)

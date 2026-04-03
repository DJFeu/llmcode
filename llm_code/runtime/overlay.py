"""Copy-on-Write overlay filesystem for speculative tool execution."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class OverlayFS:
    """A lightweight Copy-on-Write overlay that mirrors writes to a tmpdir.

    Reads check the overlay first, falling back to the real filesystem.
    ``commit()`` copies all pending overlay files to their real paths.
    ``discard()`` deletes the tmpdir without touching the real filesystem.
    """

    def __init__(self, base_dir: Path, session_id: str) -> None:
        self._base_dir = base_dir
        self._session_id = session_id
        self._tmp_root = Path(tempfile.gettempdir()) / "llm-code-overlay"
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self.overlay_dir: Path = self._tmp_root / session_id
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        # Tracks real-path → overlay-mirror-path mappings
        self._pending: dict[Path, Path] = {}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def write(self, path: Path, content: str) -> None:
        """Write *content* to the overlay mirror of *path*.

        The real filesystem is not touched.

        Parameters
        ----------
        path:
            Absolute path on the real filesystem (write destination after commit).
        content:
            UTF-8 text content to stage in the overlay.

        Raises
        ------
        ValueError
            If *path* is not absolute.
        """
        if not path.is_absolute():
            raise ValueError(f"path must be absolute, got: {path!r}")
        mirror = self._mirror_path(path)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(content, encoding="utf-8")
        self._pending[path.resolve()] = mirror

    def read(self, path: Path) -> str:
        """Read content, checking the overlay first then the real filesystem.

        Parameters
        ----------
        path:
            Absolute path to read.

        Returns
        -------
        str
            UTF-8 text content.

        Raises
        ------
        FileNotFoundError
            If *path* is absent from both overlay and real filesystem.
        """
        resolved = path.resolve()
        if resolved in self._pending:
            return self._pending[resolved].read_text(encoding="utf-8")
        if path.exists():
            return path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"File not found in overlay or real FS: {path}")

    def commit(self) -> None:
        """Copy all pending overlay files to their real-filesystem paths.

        Parent directories are created as needed. The overlay tmpdir is
        *not* removed by this call; call ``discard()`` afterwards if desired.
        """
        for real_path, mirror_path in self._pending.items():
            real_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mirror_path, real_path)

    def discard(self) -> None:
        """Delete the overlay tmpdir without writing to the real filesystem."""
        if self.overlay_dir.exists():
            shutil.rmtree(self.overlay_dir, ignore_errors=True)
        self._pending.clear()

    def list_pending(self) -> list[Path]:
        """Return the list of real paths that have been staged in the overlay."""
        return list(self._pending.keys())

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "OverlayFS":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self.discard()
        return False  # never suppress exceptions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mirror_path(self, real_path: Path) -> Path:
        """Translate a real absolute path to its overlay mirror path."""
        # Strip the leading separator so we can join with overlay_dir
        try:
            rel = real_path.resolve().relative_to("/")
        except ValueError:
            # On Windows paths like C:\...; use str-based stripping
            rel = Path(str(real_path.resolve()).lstrip("/\\"))
        return self.overlay_dir / rel

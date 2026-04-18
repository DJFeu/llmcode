"""SEA / binary integrity manifest (M10).

Produces a SHA-256 manifest of every file under a release root; the
packaging pipeline ships it alongside the binary, runtime verifies
on boot. Detects tamper, partial downloads, and silent file
corruption. File format is the standard ``sha256sum``-compatible
text file so existing toolchains consume it for free.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def build_manifest(root: Path) -> dict[str, str]:
    """Return ``{relpath: sha256_hex}`` for every regular file under ``root``."""
    manifest: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def verify_manifest(root: Path, manifest: dict[str, str]) -> bool:
    """Return True if every file in ``manifest`` is present + hash-matches."""
    for rel, want_hex in manifest.items():
        full = Path(root) / rel
        if not full.is_file():
            return False
        got_hex = hashlib.sha256(full.read_bytes()).hexdigest()
        if got_hex != want_hex:
            return False
    return True


def write_manifest_file(manifest: dict[str, str], path: Path) -> None:
    lines = [f"{sha}  {rel}" for rel, sha in sorted(manifest.items())]
    Path(path).write_text("\n".join(lines) + "\n")


def read_manifest_file(path: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, rel = parts
        manifest[rel] = sha
    return manifest

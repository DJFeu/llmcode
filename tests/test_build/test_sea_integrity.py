"""M10 — SEA / binary integrity manifest (sha256sum-compatible)."""
from __future__ import annotations

from pathlib import Path

from llm_code.build.sea import (
    build_manifest,
    read_manifest_file,
    verify_manifest,
    write_manifest_file,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestBuildManifest:
    def test_manifest_hashes_every_regular_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        _write(tmp_path / "sub" / "b.txt", "beta")
        manifest = build_manifest(tmp_path)
        assert set(manifest.keys()) == {"a.txt", str(Path("sub") / "b.txt")}
        # Hex digests are 64 chars each.
        assert all(len(v) == 64 for v in manifest.values())

    def test_manifest_is_deterministic(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        _write(tmp_path / "b.txt", "beta")
        assert build_manifest(tmp_path) == build_manifest(tmp_path)

    def test_empty_root_yields_empty_manifest(self, tmp_path: Path) -> None:
        assert build_manifest(tmp_path) == {}


class TestVerifyManifest:
    def test_verifies_untouched_tree(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        manifest = build_manifest(tmp_path)
        assert verify_manifest(tmp_path, manifest) is True

    def test_detects_content_tamper(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        _write(f, "alpha")
        manifest = build_manifest(tmp_path)
        f.write_text("tampered")
        assert verify_manifest(tmp_path, manifest) is False

    def test_detects_missing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        _write(f, "alpha")
        manifest = build_manifest(tmp_path)
        f.unlink()
        assert verify_manifest(tmp_path, manifest) is False


class TestWriteReadRoundTrip:
    def test_roundtrip_matches_sha256sum_format(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        _write(tmp_path / "b.txt", "beta")
        manifest = build_manifest(tmp_path)

        manifest_path = tmp_path / "MANIFEST"
        write_manifest_file(manifest, manifest_path)

        # Format: "<sha>  <rel>\n"
        text = manifest_path.read_text()
        for line in text.strip().splitlines():
            parts = line.split(maxsplit=1)
            assert len(parts) == 2
            assert len(parts[0]) == 64

        loaded = read_manifest_file(manifest_path)
        assert loaded == manifest

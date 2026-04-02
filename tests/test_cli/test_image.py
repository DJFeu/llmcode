"""Tests for CLI image loading utilities."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from llm_code.cli.image import detect_image_references, load_image_from_path


# Minimal PNG: 1x1 red pixel
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Minimal JPEG: smallest valid JPEG bytes
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xf5\x0a\xff\xd9"
)


class TestLoadImageFromPath:
    def test_load_png(self, tmp_path: Path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(_MINIMAL_PNG)

        result = load_image_from_path(str(img_path))

        assert result.media_type == "image/png"
        assert result.data == base64.b64encode(_MINIMAL_PNG).decode("ascii")

    def test_load_jpeg(self, tmp_path: Path):
        img_path = tmp_path / "photo.jpg"
        img_path.write_bytes(_MINIMAL_JPEG)

        result = load_image_from_path(str(img_path))

        assert result.media_type == "image/jpeg"
        assert result.data == base64.b64encode(_MINIMAL_JPEG).decode("ascii")

    def test_load_jpeg_extension(self, tmp_path: Path):
        img_path = tmp_path / "photo.jpeg"
        img_path.write_bytes(_MINIMAL_JPEG)

        result = load_image_from_path(str(img_path))
        assert result.media_type == "image/jpeg"

    def test_load_missing_raises(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.png"
        with pytest.raises(FileNotFoundError):
            load_image_from_path(str(missing))

    def test_unknown_extension_defaults_to_png(self, tmp_path: Path):
        img_path = tmp_path / "image.bmp"
        img_path.write_bytes(b"BM fake bitmap data")

        result = load_image_from_path(str(img_path))
        assert result.media_type == "image/png"

    def test_data_is_base64_string(self, tmp_path: Path):
        img_path = tmp_path / "test.png"
        img_path.write_bytes(_MINIMAL_PNG)

        result = load_image_from_path(str(img_path))
        # Should decode without error
        decoded = base64.b64decode(result.data)
        assert decoded == _MINIMAL_PNG


class TestDetectImageReferences:
    def test_returns_text_unchanged(self):
        text = "explain this code to me"
        result_text, images = detect_image_references(text)
        assert result_text == text
        assert images == []

    def test_no_images_detected(self):
        text = "here is a path /some/file.png but no /image command"
        result_text, images = detect_image_references(text)
        assert result_text == text
        assert images == []

    def test_empty_string(self):
        text = ""
        result_text, images = detect_image_references(text)
        assert result_text == ""
        assert images == []

    def test_returns_tuple(self):
        result = detect_image_references("hello")
        assert isinstance(result, tuple)
        assert len(result) == 2

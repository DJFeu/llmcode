"""Image loading utilities for the CLI layer."""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

from llm_code.api.types import ImageBlock


# Map file extensions to MIME types
_EXT_TO_MEDIA_TYPE: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def load_image_from_path(path: str) -> ImageBlock:
    """Load an image from a file path.

    Reads the file, base64-encodes it, and detects the media type from extension.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not a supported image type.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    ext = file_path.suffix.lower()
    media_type = _EXT_TO_MEDIA_TYPE.get(ext)
    if media_type is None:
        # Default to png for unknown extensions
        media_type = "image/png"

    raw = file_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")

    return ImageBlock(media_type=media_type, data=encoded)


def capture_clipboard_image() -> ImageBlock | None:
    """Capture an image from the clipboard.

    macOS: uses pngpaste -
    Linux: uses xclip -selection clipboard -t image/png -o

    Returns None if capture is not available or fails.
    """
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["pngpaste", "-"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                encoded = base64.b64encode(result.stdout).decode("ascii")
                return ImageBlock(media_type="image/png", data=encoded)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None
    else:
        # Linux
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                encoded = base64.b64encode(result.stdout).decode("ascii")
                return ImageBlock(media_type="image/png", data=encoded)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def extract_dropped_images(text: str) -> tuple[str, list[ImageBlock]]:
    r"""Detect drag-and-dropped image file paths in user input.

    Terminal drag-and-drop produces paths like:
      /Users/adam/screenshot.png
      '/Users/adam/my screenshot.png'
      /Users/adam/my\ screenshot.png

    Returns (cleaned_text, list_of_ImageBlocks).
    """
    import shlex
    from pathlib import Path as P

    images: list[ImageBlock] = []
    remaining_parts: list[str] = []

    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        path = P(token)
        if path.suffix.lower() in _IMAGE_EXTENSIONS and path.is_file():
            try:
                img = load_image_from_path(str(path))
                images.append(img)
            except Exception:
                remaining_parts.append(token)
        else:
            remaining_parts.append(token)

    return " ".join(remaining_parts), images

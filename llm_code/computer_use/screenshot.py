"""Cross-platform screenshot capture."""
from __future__ import annotations

import base64
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple


def take_screenshot(region: Tuple[int, int, int, int] | None = None) -> bytes:
    """Capture the screen and return raw PNG bytes.

    Args:
        region: Optional (x, y, width, height) crop region.

    Returns:
        PNG image bytes.

    Platform strategy:
        macOS  -> screencapture CLI
        Linux  -> scrot CLI
        Windows -> mss library (lazy import)
    """
    system = platform.system()

    if system == "Darwin":
        return _capture_macos(region)
    elif system == "Linux":
        return _capture_linux(region)
    elif system == "Windows":
        return _capture_windows(region)
    else:
        raise RuntimeError(f"Unsupported platform for screenshots: {system}")


def take_screenshot_base64(region: Tuple[int, int, int, int] | None = None) -> str:
    """Capture screen and return as a base64-encoded string."""
    raw = take_screenshot(region)
    return base64.b64encode(raw).decode("ascii")


def _capture_macos(region: Tuple[int, int, int, int] | None) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = ["screencapture", "-x"]
    if region:
        x, y, w, h = region
        cmd.extend(["-R", f"{x},{y},{w},{h}"])
    cmd.append(tmp_path)

    subprocess.run(cmd, check=True, timeout=10)
    data = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    return data


def _capture_linux(region: Tuple[int, int, int, int] | None) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = ["scrot"]
    if region:
        x, y, w, h = region
        cmd.extend(["-a", f"{x},{y},{w},{h}"])
    cmd.append(tmp_path)

    subprocess.run(cmd, check=True, timeout=10)
    data = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    return data


def _capture_windows(region: Tuple[int, int, int, int] | None) -> bytes:
    try:
        import mss
        import mss.tools
    except ImportError as exc:
        raise RuntimeError(
            "mss is required for Windows screenshots. "
            "Install with: pip install llm-code[computer-use]"
        ) from exc

    with mss.mss() as sct:
        if region:
            x, y, w, h = region
            monitor = {"top": y, "left": x, "width": w, "height": h}
        else:
            monitor = sct.monitors[1]  # Primary monitor
        img = sct.grab(monitor)
        return mss.tools.to_png(img.rgb, img.size)

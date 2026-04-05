"""Detect the frontmost application on macOS."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class AppInfo:
    """Information about a running application."""
    name: str
    bundle_id: str
    pid: int


def _get_via_osascript() -> AppInfo:
    """Use osascript to get frontmost app info."""
    script = (
        'tell application "System Events" to '
        'set fp to first process whose frontmost is true\n'
        'set n to name of fp\n'
        'set b to bundle identifier of fp\n'
        'set p to unix id of fp\n'
        'return n & "|" & b & "|" & (p as text)'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr}")
    parts = result.stdout.strip().split("|")
    if len(parts) < 3:
        raise RuntimeError(f"Unexpected osascript output: {result.stdout}")
    return AppInfo(name=parts[0], bundle_id=parts[1], pid=int(parts[2]))


def get_frontmost_app_sync() -> AppInfo:
    """Get frontmost app, with fallback to Unknown on any error."""
    try:
        return _get_via_osascript()
    except Exception:
        return AppInfo(name="Unknown", bundle_id="", pid=0)


async def get_frontmost_app() -> AppInfo:
    """Async wrapper for get_frontmost_app_sync."""
    return await asyncio.to_thread(get_frontmost_app_sync)

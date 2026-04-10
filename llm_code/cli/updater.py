"""Self-update utilities for llmcode-cli.

Provides:
    - ``check_update()``: compare installed version against PyPI latest
    - ``run_upgrade()``: execute pip install --upgrade in a subprocess
    - ``check_update_background()``: non-blocking startup check

Design:
    - PyPI JSON API (no auth, no dependencies)
    - Subprocess for pip upgrade (no importlib tricks)
    - Background check caches result for 6 hours to avoid spamming PyPI
    - All functions are async-safe
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PACKAGE_NAME = "llmcode-cli"
_PYPI_URL = f"https://pypi.org/pypi/{_PACKAGE_NAME}/json"
_CHECK_CACHE_FILE = Path.home() / ".llmcode" / ".update_check"
_CHECK_INTERVAL_SECONDS = 6 * 3600  # 6 hours


def _get_installed_version() -> str:
    """Return the installed version of llmcode-cli."""
    try:
        from importlib.metadata import version
        return version(_PACKAGE_NAME)
    except Exception:
        return "0.0.0"


async def _fetch_latest_version() -> str | None:
    """Fetch the latest version from PyPI via subprocess.

    Uses sys.executable to run a small Python script that calls
    urllib.request — avoids importing httpx/requests at module level
    and is safe against shell injection (no shell=True, no string
    interpolation into command args).
    """
    script = (
        "import urllib.request, json, sys\n"
        "try:\n"
        f"    r = urllib.request.urlopen('{_PYPI_URL}', timeout=5)\n"
        "    print(json.loads(r.read())['info']['version'])\n"
        "except Exception as e:\n"
        "    print(f'ERROR:{e}', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception as e:
        logger.debug("PyPI version check failed: %s", e)
    return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a tuple for comparison."""
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


async def check_update() -> tuple[str, str] | None:
    """Check if a newer version is available on PyPI.

    Returns ``(current_version, latest_version)`` if update available,
    or ``None`` if already up to date (or check failed).
    """
    current = _get_installed_version()
    latest = await _fetch_latest_version()
    if latest is None:
        return None
    if _parse_version(latest) > _parse_version(current):
        return (current, latest)
    return None


async def run_upgrade() -> tuple[bool, str]:
    """Run pip install --upgrade llmcode-cli.

    Returns ``(success, output_text)``.
    Uses subprocess with explicit argv (no shell) to prevent injection.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", _PACKAGE_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode() if stdout else ""
        return (proc.returncode == 0, output)
    except asyncio.TimeoutError:
        return (False, "Upgrade timed out after 120 seconds")
    except Exception as e:
        return (False, str(e))


async def check_update_background() -> str | None:
    """Non-blocking startup version check with 6-hour cache.

    Returns a human-readable hint like "Update available: 1.17.0 → 1.18.0"
    or None if no update / check skipped (within cache interval).
    """
    # Check cache: skip if checked recently
    try:
        if _CHECK_CACHE_FILE.exists():
            data = json.loads(_CHECK_CACHE_FILE.read_text())
            last_check = data.get("timestamp", 0)
            if time.time() - last_check < _CHECK_INTERVAL_SECONDS:
                cached_latest = data.get("latest")
                current = _get_installed_version()
                if cached_latest and _parse_version(cached_latest) > _parse_version(current):
                    return f"Update available: {current} \u2192 {cached_latest} (run /update)"
                return None
    except Exception:
        pass

    # Fetch from PyPI
    result = await check_update()

    # Cache the result
    try:
        _CHECK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "timestamp": time.time(),
            "latest": result[1] if result else _get_installed_version(),
        }
        _CHECK_CACHE_FILE.write_text(json.dumps(cache_data))
    except Exception:
        pass

    if result:
        current, latest = result
        return f"Update available: {current} \u2192 {latest} (run /update)"
    return None

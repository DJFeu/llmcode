"""VRAM and system memory detection across platforms."""
from __future__ import annotations

import subprocess
import sys


def detect_vram_gb() -> float | None:
    """Detect available VRAM/memory in GB.

    Detection chain (first success wins):
    1. NVIDIA GPU via nvidia-smi
    2. Apple Silicon unified memory via sysctl (× 0.75)
    3. Linux /proc/meminfo (× 0.5)
    4. None if all fail
    """
    result = _detect_nvidia()
    if result is not None:
        return result

    result = _detect_apple_silicon()
    if result is not None:
        return result

    result = _detect_linux_meminfo()
    if result is not None:
        return result

    return None


def _detect_nvidia() -> float | None:
    """Detect NVIDIA GPU VRAM via nvidia-smi."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return None
        first_line = proc.stdout.strip().split("\n")[0].strip()
        mib = float(first_line.replace(" MiB", ""))
        return mib / 1024.0
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None


def _detect_apple_silicon() -> float | None:
    """Detect Apple Silicon unified memory via sysctl."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return None
        mem_bytes = int(proc.stdout.strip())
        gb = mem_bytes / (1024**3)
        return gb * 0.75
    except (FileNotFoundError, OSError, ValueError):
        return None


def _detect_linux_meminfo() -> float | None:
    """Detect total RAM from /proc/meminfo on Linux."""
    if sys.platform != "linux":
        return None
    try:
        with open("/proc/meminfo") as f:
            content = f.read()
        for line in content.split("\n"):
            if line.startswith("MemTotal:"):
                parts = line.split()
                kb = int(parts[1])
                gb = kb / (1024**2)
                return gb * 0.5
        return None
    except (FileNotFoundError, OSError, ValueError):
        return None

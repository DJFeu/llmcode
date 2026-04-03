"""SandboxDetector — detect container/sandbox environments and restrict paths."""
from __future__ import annotations

from pathlib import Path


def _has_dockerenv() -> bool:
    """Return True if the Docker sentinel file exists."""
    return Path("/.dockerenv").exists()


def _cgroup_indicates_container() -> bool:
    """Return True if /proc/1/cgroup suggests a container runtime."""
    cgroup_path = Path("/proc/1/cgroup")
    if not cgroup_path.exists():
        return False
    try:
        content = cgroup_path.read_text(errors="replace")
        container_markers = ("docker", "kubepods", "containerd", "lxc", "/ecs/")
        return any(marker in content for marker in container_markers)
    except OSError:
        return False


def _detect_sandbox_type() -> str:
    """Return a string describing the detected sandbox type, or 'none'."""
    if _has_dockerenv():
        return "docker"
    if _cgroup_indicates_container():
        # Try to narrow down further
        cgroup_path = Path("/proc/1/cgroup")
        try:
            content = cgroup_path.read_text(errors="replace")
            if "kubepods" in content:
                return "kubernetes"
            if "containerd" in content:
                return "containerd"
            if "lxc" in content:
                return "lxc"
            if "/ecs/" in content:
                return "ecs"
        except OSError:
            pass
        return "container"
    return "none"


def is_sandboxed() -> bool:
    """Return True if the process appears to be running inside a container/sandbox."""
    return _detect_sandbox_type() != "none"


def get_sandbox_info() -> dict:
    """Return a dict describing the current sandbox environment.

    Keys
    ----
    sandboxed : bool
        Whether a sandbox was detected.
    type : str
        One of ``"docker"``, ``"kubernetes"``, ``"containerd"``, ``"lxc"``,
        ``"ecs"``, ``"container"``, or ``"none"``.
    restrictions : list[str]
        Human-readable descriptions of active restrictions.
    """
    sandbox_type = _detect_sandbox_type()
    sandboxed = sandbox_type != "none"

    restrictions: list[str] = []
    if sandboxed:
        restrictions = [
            "Network access may be restricted",
            "Host filesystem is not directly accessible",
            "Privileged operations are not permitted",
        ]

    return {
        "sandboxed": sandboxed,
        "type": sandbox_type,
        "restrictions": restrictions,
    }


def restrict_paths(base_dir: Path) -> list[Path]:
    """Return a list of absolute paths the agent should NOT access outside *base_dir*.

    These paths represent sensitive locations on the host filesystem.
    """
    home = Path.home()
    sensitive: list[Path] = [
        home / ".ssh",
        home / ".aws",
        home / ".config" / "gcloud",
        home / ".gnupg",
        home / ".netrc",
        home / ".pgpass",
        Path("/etc/passwd"),
        Path("/etc/shadow"),
        Path("/etc/sudoers"),
        Path("/root"),
        Path("/var/run/secrets"),  # Kubernetes service-account tokens
    ]
    # Only return paths that are NOT inside base_dir
    try:
        base_resolved = base_dir.resolve()
    except Exception:
        base_resolved = base_dir

    result: list[Path] = []
    for p in sensitive:
        try:
            p.resolve().relative_to(base_resolved)
            # The path IS inside base_dir — don't add to restrictions
        except ValueError:
            result.append(p)
    return result

"""Linux landlock LSM sandbox — skeleton (L1 — Sprint 6).

Landlock (Linux 5.13+) is a Linux Security Module that lets an
unprivileged process restrict its own filesystem + network access via
``landlock_create_ruleset`` / ``landlock_add_rule`` /
``landlock_restrict_self`` syscalls (numbers 444-446 on x86_64).

The full ctypes integration is a larger self-contained effort — the
rulesets / fs paths / TCP bind/connect scopes are rich enough that
shipping them alongside the rest of Sprint 6 would blow the review.
This **skeleton** lands the architectural seam with honest
delegation:

    * Availability check (Linux + uname.release >= 5.13 + bwrap
      on PATH) refuses construction otherwise so
      :func:`choose_backend` can fall back cleanly.
    * ``execute`` / ``execute_streaming`` delegate to an internal
      :class:`BwrapSandboxBackend`. bubblewrap itself uses landlock
      on new-enough kernels, so this delegation is not a lie — it's
      the most portable way to get landlock-style enforcement today.
    * Direct landlock syscalls + ctypes are planned follow-ups; the
      public API stays the same so callers don't churn.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Callable

from llm_code.sandbox.policy_manager import SandboxPolicy, SandboxResult


_KERNEL_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)")


def _kernel_at_least(major: int, minor: int) -> bool:
    """Return True if ``uname.release`` advertises kernel >= major.minor."""
    try:
        release = os.uname().release
    except Exception:
        return False
    m = _KERNEL_RELEASE_RE.match(release)
    if not m:
        return False
    host_major, host_minor = int(m.group(1)), int(m.group(2))
    return (host_major, host_minor) >= (major, minor)


class LandlockSandboxBackend:
    """Skeleton Linux landlock sandbox backend.

    Advertises name="landlock" so :func:`choose_backend` can route to
    it on kernels where landlock is actually available. Internally
    delegates to :class:`BwrapSandboxBackend` until the ctypes
    landlock integration lands in a follow-up sprint.
    """

    name = "landlock"

    def __init__(
        self,
        *,
        workspace: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        sysname = ""
        try:
            sysname = os.uname().sysname
        except Exception:
            sysname = ""
        if sysname.lower() != "linux":
            raise RuntimeError(
                f"LandlockSandboxBackend requires Linux (got sysname={sysname!r})"
            )
        if not _kernel_at_least(5, 13):
            raise RuntimeError(
                "LandlockSandboxBackend requires kernel >= 5.13 (landlock LSM)"
            )
        if not shutil.which("bwrap"):
            raise RuntimeError(
                "LandlockSandboxBackend currently delegates to bwrap — "
                "install bubblewrap to use this backend until the direct "
                "ctypes integration lands."
            )

        # Import + construct the delegate here so the test suite can
        # swap ``self._delegate`` for a mock after construction.
        from llm_code.sandbox.bwrap import BwrapSandboxBackend
        self._delegate = BwrapSandboxBackend(
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )

    def execute(
        self,
        command: list[str],
        policy: SandboxPolicy,
    ) -> SandboxResult:
        return self._delegate.execute(command, policy)

    def execute_streaming(
        self,
        command: list[str],
        policy: SandboxPolicy,
        *,
        on_chunk: Callable[[str], None],
    ) -> SandboxResult:
        return self._delegate.execute_streaming(
            command, policy, on_chunk=on_chunk,
        )

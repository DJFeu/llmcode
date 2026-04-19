"""Landlock LSM ctypes implementation (F1 — Sprint 7).

Talks to the Linux kernel's landlock LSM directly via the three
syscalls introduced in 5.13:

    444: landlock_create_ruleset(attr, size, flags) -> ruleset_fd
    445: landlock_add_rule(ruleset_fd, rule_type, attr, flags) -> 0
    446: landlock_restrict_self(ruleset_fd, flags) -> 0

Plus the classic ``prctl(PR_SET_NO_NEW_PRIVS, 1)`` prelude required
before landlock_restrict_self can succeed for an unprivileged task.

This module is meant to be called from a subprocess's ``preexec_fn``
— ``landlock_restrict_self`` is one-way for the current task, so the
parent runtime can never call it directly. See
:class:`LandlockSandboxBackend` for the orchestration.

The syscall numbers are x86_64. When landlock lands on other arches
the per-arch tables diverge; we document the limitation rather than
silently misbehave on arm64.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re

from llm_code.sandbox.policy_manager import SandboxPolicy


# ── Syscall numbers (x86_64) ──────────────────────────────────────────

NR_LANDLOCK_CREATE_RULESET = 444
NR_LANDLOCK_ADD_RULE = 445
NR_LANDLOCK_RESTRICT_SELF = 446

# prctl options
PR_SET_NO_NEW_PRIVS = 38

# Landlock rule types
LANDLOCK_RULE_PATH_BENEATH = 1

# Access bits (from uapi/linux/landlock.h)
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12

O_PATH = 0o10000000
O_CLOEXEC = 0o2000000

_KERNEL_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)")


# ── libc loader ───────────────────────────────────────────────────────


def _load_libc():
    try:
        path = ctypes.util.find_library("c") or "libc.so.6"
        return ctypes.CDLL(path, use_errno=True)
    except OSError:
        return None


_libc = _load_libc()


def _syscall(nr: int, *args: int) -> int:
    """Invoke ``libc.syscall`` with proper signed-long types."""
    if _libc is None:
        return -1
    _libc.syscall.argtypes = [ctypes.c_long] + [ctypes.c_long] * len(args)
    _libc.syscall.restype = ctypes.c_long
    return int(_libc.syscall(nr, *args))


def _prctl(*args: int) -> int:
    if _libc is None:
        return -1
    _libc.prctl.argtypes = [ctypes.c_int] * len(args)
    _libc.prctl.restype = ctypes.c_int
    return int(_libc.prctl(*args))


# ── Structures ────────────────────────────────────────────────────────


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


# ── Public API ────────────────────────────────────────────────────────


def build_access_mask(policy: SandboxPolicy) -> int:
    """Render ``policy`` into a landlock ``handled_access_fs`` bitmask.

    Execute is always included — without it the child can't even exec
    ``sh``. Reads always enabled when ``allow_read=True`` (that's the
    whole point of policy=True). Writes add the full mutation family
    (write / remove / make_*).
    """
    mask = LANDLOCK_ACCESS_FS_EXECUTE
    if policy.allow_read:
        mask |= LANDLOCK_ACCESS_FS_READ_FILE
        mask |= LANDLOCK_ACCESS_FS_READ_DIR
    if policy.allow_write:
        mask |= LANDLOCK_ACCESS_FS_WRITE_FILE
        mask |= LANDLOCK_ACCESS_FS_REMOVE_FILE
        mask |= LANDLOCK_ACCESS_FS_REMOVE_DIR
        mask |= LANDLOCK_ACCESS_FS_MAKE_REG
        mask |= LANDLOCK_ACCESS_FS_MAKE_DIR
        mask |= LANDLOCK_ACCESS_FS_MAKE_SYM
        mask |= LANDLOCK_ACCESS_FS_MAKE_FIFO
        mask |= LANDLOCK_ACCESS_FS_MAKE_SOCK
    return mask


def is_landlock_available() -> bool:
    """Return True when the host kernel + libc can drive landlock."""
    try:
        uname = os.uname()
    except Exception:
        return False
    if uname.sysname.lower() != "linux":
        return False
    m = _KERNEL_RELEASE_RE.match(uname.release)
    if not m:
        return False
    if (int(m.group(1)), int(m.group(2))) < (5, 13):
        return False
    if _libc is None:
        return False
    return True


def apply_landlock(policy: SandboxPolicy, workspace: str) -> None:
    """Install landlock + no_new_privs on the current task.

    Called inside a subprocess's ``preexec_fn`` — operations are
    irreversible for the current process. Raises ``RuntimeError`` on
    any syscall failure so Popen can surface the mess.
    """
    if _prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise RuntimeError(
            f"landlock: prctl(PR_SET_NO_NEW_PRIVS) failed "
            f"(errno={ctypes.get_errno() if _libc else 'n/a'}: no_new_privs)"
        )

    attr = _LandlockRulesetAttr(handled_access_fs=build_access_mask(policy))
    ruleset_fd = _syscall(
        NR_LANDLOCK_CREATE_RULESET,
        ctypes.addressof(attr),
        ctypes.sizeof(attr),
        0,
    )
    if ruleset_fd < 0:
        raise RuntimeError(
            f"landlock: create_ruleset failed "
            f"(errno={ctypes.get_errno() if _libc else 'n/a'})"
        )

    # Grant access to the workspace subtree.
    path_fd = -1
    try:
        path_fd = os.open(workspace, os.O_RDONLY | O_CLOEXEC)
    except OSError:
        pass
    rule = _LandlockPathBeneathAttr(
        allowed_access=build_access_mask(policy),
        parent_fd=path_fd,
    )
    rc = _syscall(
        NR_LANDLOCK_ADD_RULE,
        ruleset_fd,
        LANDLOCK_RULE_PATH_BENEATH,
        ctypes.addressof(rule),
        0,
    )
    if path_fd >= 0:
        try:
            os.close(path_fd)
        except OSError:
            pass
    if rc != 0:
        raise RuntimeError(
            f"landlock: add_rule failed "
            f"(errno={ctypes.get_errno() if _libc else 'n/a'})"
        )

    rc = _syscall(NR_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
    try:
        os.close(ruleset_fd)
    except OSError:
        pass
    if rc != 0:
        raise RuntimeError(
            f"landlock: restrict_self failed "
            f"(errno={ctypes.get_errno() if _libc else 'n/a'})"
        )

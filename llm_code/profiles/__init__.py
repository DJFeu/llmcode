"""Helpers for working with bundled and user-installed model profiles.

The runtime profile *registry* lives in
:mod:`llm_code.runtime.model_profile` and
:mod:`llm_code.runtime.profile_registry`. This package adds a thin
shell around the wheel-bundled assets in
:mod:`llm_code._builtins.profiles` so the ``llmcode profiles`` CLI
group (v2.10.0) can list / diff / update them without reaching into
runtime internals.
"""
from llm_code.profiles.builtins import (
    builtin_profile_dir,
    builtin_profile_path,
    list_builtin_profile_paths,
)

__all__ = [
    "builtin_profile_dir",
    "builtin_profile_path",
    "list_builtin_profile_paths",
]

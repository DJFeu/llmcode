"""Wave2-6: unified dialog abstraction for interactive prompts.

The TUI (and anything that used to hand-roll confirm / select / text
input loops) talks to a single ``Dialogs`` Protocol. Three backends
satisfy the protocol with different latency / interactivity trade-offs:

* ``ScriptedDialogs`` — deterministic, no I/O, pre-enqueued responses.
  Used by tests to replace ad-hoc input mocks with a typed queue.
* ``HeadlessDialogs`` — stdin/stdout line-based prompts for CI, pipe
  mode, ``--yes`` runs, and SSH sessions without a real terminal.
* ``TextualDialogs`` — modal screens inside the Textual app, pushed
  via ``push_screen_wait()`` for async confirm / select / text / checklist.

Import the Protocol from here; import concrete backends from their
submodules. Callers should depend on the Protocol so swapping
backends at runtime is a one-line change.
"""
from llm_code.tui.dialogs.api import (
    Choice,
    Dialogs,
    DialogCancelled,
    DialogValidationError,
)
from llm_code.tui.dialogs.headless import HeadlessDialogs
from llm_code.tui.dialogs.scripted import ScriptedDialogs
from llm_code.tui.dialogs.textual_backend import TextualDialogs

__all__ = [
    "Choice",
    "DialogCancelled",
    "DialogValidationError",
    "Dialogs",
    "HeadlessDialogs",
    "ScriptedDialogs",
    "TextualDialogs",
]

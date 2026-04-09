"""Wave2-6: Test backend — pre-enqueued responses, zero I/O.

Replaces ad-hoc input mocks in tests. A test stages the expected
responses with ``push_*`` helpers and any dialog call pops the head
of the queue. Unused responses at teardown are a test failure —
that catches the "test enqueued a response the code never actually
asked for" mistake.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Sequence, TypeVar

from llm_code.tui.dialogs.api import (
    Choice,
    DialogCancelled,
    DialogValidationError,
    TextValidator,
)

T = TypeVar("T")


class ScriptedDialogs:
    """Deterministic Dialogs impl for unit tests.

    Stage responses in the order they will be consumed::

        dialogs = ScriptedDialogs()
        dialogs.push_confirm(True)
        dialogs.push_select("claude-sonnet-4-6")
        dialogs.push_text("refactor the parser")
        await runtime.some_method_that_uses(dialogs)
        dialogs.assert_drained()  # raises if any response unused

    Raising :class:`DialogCancelled` is modeled with
    ``push_cancel(method_name)`` so a test can exercise the cancel
    path without pushing a real value.
    """

    def __init__(self) -> None:
        self._confirm_queue: deque[bool | type] = deque()
        self._select_queue: deque[Any] = deque()
        self._text_queue: deque[str | type] = deque()
        self._checklist_queue: deque[list[Any]] = deque()
        self.calls: list[tuple[str, str]] = []  # (method, prompt) for assertions

    # ------------------------------------------------------------------
    # Staging helpers
    # ------------------------------------------------------------------

    def push_confirm(self, answer: bool) -> None:
        self._confirm_queue.append(answer)

    def push_select(self, value: Any) -> None:
        self._select_queue.append(value)

    def push_text(self, value: str) -> None:
        self._text_queue.append(value)

    def push_checklist(self, values: list[Any]) -> None:
        self._checklist_queue.append(list(values))

    def push_cancel(self, method: str) -> None:
        """Queue a DialogCancelled for the next call to *method*.

        ``method`` must be one of confirm / select / text / checklist.
        """
        queue = self._queue_for(method)
        queue.append(DialogCancelled)

    # ------------------------------------------------------------------
    # Dialogs protocol implementation
    # ------------------------------------------------------------------

    async def confirm(
        self,
        prompt: str,
        *,
        default: bool = False,
        danger: bool = False,
    ) -> bool:
        self.calls.append(("confirm", prompt))
        if not self._confirm_queue:
            raise AssertionError(
                f"ScriptedDialogs.confirm({prompt!r}) called but no "
                f"response was enqueued; use push_confirm() first."
            )
        value = self._confirm_queue.popleft()
        if value is DialogCancelled:
            raise DialogCancelled(f"scripted cancel on confirm: {prompt}")
        return bool(value)

    async def select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        *,
        default: T | None = None,
    ) -> T:
        self.calls.append(("select", prompt))
        if not self._select_queue:
            raise AssertionError(
                f"ScriptedDialogs.select({prompt!r}) called but no "
                f"response was enqueued; use push_select() first."
            )
        value = self._select_queue.popleft()
        if value is DialogCancelled:
            raise DialogCancelled(f"scripted cancel on select: {prompt}")
        # Sanity: pushed value must be one of the non-disabled choices.
        valid = {c.value for c in choices if not c.disabled}
        if valid and value not in valid:
            raise AssertionError(
                f"ScriptedDialogs.select({prompt!r}): enqueued value "
                f"{value!r} is not a valid choice. Valid: {sorted(map(repr, valid))}"
            )
        return value  # type: ignore[return-value]

    async def text(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,
        validator: TextValidator | None = None,
    ) -> str:
        self.calls.append(("text", prompt))
        if not self._text_queue:
            raise AssertionError(
                f"ScriptedDialogs.text({prompt!r}) called but no "
                f"response was enqueued; use push_text() first."
            )
        value = self._text_queue.popleft()
        if value is DialogCancelled:
            raise DialogCancelled(f"scripted cancel on text: {prompt}")
        text_value = str(value)
        if validator is not None:
            error = validator(text_value)
            if error is not None:
                # Propagate rather than retry — test paths should
                # push the valid value on first try, or push an
                # explicit cancel. Anything else is an invariant
                # failure in the test itself.
                raise DialogValidationError(error)
        return text_value

    async def checklist(
        self,
        prompt: str,
        items: Sequence[Choice[T]],
        *,
        min_select: int = 0,
        max_select: int | None = None,
    ) -> list[T]:
        self.calls.append(("checklist", prompt))
        if not self._checklist_queue:
            raise AssertionError(
                f"ScriptedDialogs.checklist({prompt!r}) called but no "
                f"response was enqueued; use push_checklist() first."
            )
        values = self._checklist_queue.popleft()
        # Sanity: bounds and membership
        if len(values) < min_select:
            raise AssertionError(
                f"ScriptedDialogs.checklist({prompt!r}): enqueued "
                f"{len(values)} values but min_select={min_select}"
            )
        if max_select is not None and len(values) > max_select:
            raise AssertionError(
                f"ScriptedDialogs.checklist({prompt!r}): enqueued "
                f"{len(values)} values but max_select={max_select}"
            )
        valid = {c.value for c in items if not c.disabled}
        for v in values:
            if v not in valid:
                raise AssertionError(
                    f"ScriptedDialogs.checklist({prompt!r}): enqueued "
                    f"value {v!r} is not a valid choice."
                )
        return values  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def assert_drained(self) -> None:
        """Raise AssertionError if any enqueued response was never consumed.

        Called at test teardown to catch the "test enqueued a response
        the code never actually asked for" bug. A silently-unused
        response usually means the production code path diverged from
        what the test expected.
        """
        leftovers: list[str] = []
        if self._confirm_queue:
            leftovers.append(f"confirm: {list(self._confirm_queue)}")
        if self._select_queue:
            leftovers.append(f"select: {list(self._select_queue)}")
        if self._text_queue:
            leftovers.append(f"text: {list(self._text_queue)}")
        if self._checklist_queue:
            leftovers.append(f"checklist: {list(self._checklist_queue)}")
        if leftovers:
            raise AssertionError(
                "ScriptedDialogs still had unconsumed responses at teardown: "
                + "; ".join(leftovers)
            )

    def _queue_for(self, method: str) -> deque:
        queues = {
            "confirm": self._confirm_queue,
            "select": self._select_queue,
            "text": self._text_queue,
            "checklist": self._checklist_queue,
        }
        if method not in queues:
            raise ValueError(
                f"unknown dialog method {method!r}; expected one of "
                f"{sorted(queues.keys())}"
            )
        return queues[method]

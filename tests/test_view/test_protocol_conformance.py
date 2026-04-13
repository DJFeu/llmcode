"""Protocol conformance tests for ViewBackend implementations.

This module provides:
1. ``ViewBackendConformanceSuite`` — an abstract pytest test class that
   every backend subclasses and inherits. Ensures every backend honors
   the Protocol contract uniformly.
2. Per-method contract tests — assert method signatures, abstractness,
   default behaviors, and type correctness at the Protocol level
   (without needing a concrete backend).

Concrete backends (REPLBackend in M3+, TelegramBackend in v2.1+, etc.)
will import ``ViewBackendConformanceSuite`` and provide a fixture that
yields an instance, then inherit all tests for free.

Example future usage:

    class TestREPLBackendConformance(ViewBackendConformanceSuite):
        @pytest.fixture
        async def backend(self, tmp_path):
            ...
            yield REPLBackend(...)
"""
from __future__ import annotations

import inspect

import pytest

from llm_code.view.base import ViewBackend
from llm_code.view.dialog_types import (
    Choice,
    DialogCancelled,
    DialogValidationError,
)
from llm_code.view.types import (
    MessageEvent,
    Role,
    RiskLevel,
    StatusUpdate,
    StreamingMessageHandle,
    ToolEventHandle,
)


# === Protocol-level tests (no concrete backend required) ===


def test_view_backend_is_abstract():
    """ViewBackend cannot be instantiated directly."""
    with pytest.raises(TypeError, match="abstract"):
        ViewBackend()  # type: ignore[abstract]


def test_view_backend_has_expected_abstract_methods():
    """The ABC's abstractmethods set matches the spec section 5.1 list."""
    expected = {
        "start",
        "stop",
        "run",
        "request_exit",
        "set_input_handler",
        "render_message",
        "start_streaming_message",
        "start_tool_event",
        "update_status",
        "show_confirm",
        "show_select",
        "show_text_input",
        "show_checklist",
        "print_info",
        "print_warning",
        "print_error",
        "print_panel",
        "open_external_editor",
    }
    actual = set(ViewBackend.__abstractmethods__)
    assert actual == expected, (
        f"Abstract method set drift. "
        f"Missing: {expected - actual}. Extra: {actual - expected}."
    )


def test_view_backend_has_default_noop_methods():
    """These methods have default (non-abstract) implementations:
    mark_fatal_error, voice_*, clear_screen, on_turn_*, on_session_*."""
    default_methods = {
        "mark_fatal_error",
        "voice_started",
        "voice_progress",
        "voice_stopped",
        "clear_screen",
        "on_turn_start",
        "on_turn_end",
        "on_session_compaction",
        "on_session_load",
    }
    for name in default_methods:
        assert name in dir(ViewBackend), f"{name} missing from ViewBackend"
        assert name not in ViewBackend.__abstractmethods__, (
            f"{name} should have a default impl, not be abstract"
        )


def test_show_confirm_signature():
    """show_confirm takes prompt, default, risk, returns bool."""
    sig = inspect.signature(ViewBackend.show_confirm)
    params = sig.parameters
    assert "prompt" in params
    assert "default" in params and params["default"].default is False
    assert "risk" in params and params["risk"].default == RiskLevel.NORMAL


def test_show_select_signature():
    """show_select takes prompt, choices, default; returns T."""
    sig = inspect.signature(ViewBackend.show_select)
    params = sig.parameters
    assert "prompt" in params
    assert "choices" in params
    assert "default" in params and params["default"].default is None


def test_show_text_input_signature():
    """show_text_input takes prompt, default, validator, secret."""
    sig = inspect.signature(ViewBackend.show_text_input)
    params = sig.parameters
    assert "prompt" in params
    assert "default" in params and params["default"].default is None
    assert "validator" in params and params["validator"].default is None
    assert "secret" in params and params["secret"].default is False


def test_start_streaming_message_signature():
    """start_streaming_message takes role, metadata; returns handle."""
    sig = inspect.signature(ViewBackend.start_streaming_message)
    params = sig.parameters
    assert "role" in params
    assert "metadata" in params and params["metadata"].default is None


def test_start_tool_event_signature():
    """start_tool_event takes tool_name, args; returns handle."""
    sig = inspect.signature(ViewBackend.start_tool_event)
    params = sig.parameters
    assert "tool_name" in params
    assert "args" in params


# === Data type tests ===


def test_role_enum_values():
    """Role enum has exactly 4 values."""
    assert {r.value for r in Role} == {"user", "assistant", "system", "tool"}


def test_risk_level_enum_values():
    """RiskLevel enum has exactly 4 values in ascending severity."""
    expected = ["normal", "elevated", "high", "critical"]
    actual = [r.value for r in RiskLevel]
    assert actual == expected


def test_message_event_frozen():
    """MessageEvent is frozen (immutable)."""
    m = MessageEvent(role=Role.USER, content="hi")
    with pytest.raises((AttributeError, TypeError)):
        m.content = "changed"  # type: ignore[misc]


def test_message_event_defaults():
    """MessageEvent has sensible defaults."""
    from datetime import datetime
    m = MessageEvent(role=Role.ASSISTANT, content="response")
    assert m.metadata == {}
    assert isinstance(m.timestamp, datetime)


def test_status_update_partial_defaults_to_none():
    """StatusUpdate fields default to None so partial updates work."""
    s = StatusUpdate()
    assert s.model is None
    assert s.cost_usd is None
    assert s.is_streaming is False  # the one non-None default
    assert s.voice_active is False


def test_choice_frozen():
    """Choice is frozen."""
    c = Choice(value=1, label="one")
    with pytest.raises((AttributeError, TypeError)):
        c.label = "changed"  # type: ignore[misc]


def test_dialog_cancelled_is_exception():
    """DialogCancelled inherits from Exception."""
    assert issubclass(DialogCancelled, Exception)


def test_dialog_validation_error_carries_attempted_value():
    """DialogValidationError retains the rejected input."""
    err = DialogValidationError("bad email", attempted_value="nope")
    assert str(err) == "bad email"
    assert err.attempted_value == "nope"


# === Protocol runtime-check tests ===


def test_streaming_message_handle_is_runtime_checkable():
    """StreamingMessageHandle is a runtime_checkable Protocol so
    isinstance() works on duck-typed handles."""

    class FakeHandle:
        def feed(self, chunk: str) -> None: ...
        def commit(self) -> None: ...
        def abort(self) -> None: ...
        @property
        def is_active(self) -> bool: return True

    h = FakeHandle()
    assert isinstance(h, StreamingMessageHandle)


def test_tool_event_handle_is_runtime_checkable():
    """ToolEventHandle is a runtime_checkable Protocol."""

    class FakeToolHandle:
        def feed_stdout(self, line: str) -> None: ...
        def feed_stderr(self, line: str) -> None: ...
        def feed_diff(self, diff_text: str) -> None: ...
        def commit_success(self, *, summary=None, metadata=None) -> None: ...
        def commit_failure(self, *, error, exit_code=None) -> None: ...
        @property
        def is_active(self) -> bool: return True

    h = FakeToolHandle()
    assert isinstance(h, ToolEventHandle)


def test_non_conforming_object_fails_runtime_check():
    """An object missing required methods fails isinstance()."""

    class NotAHandle:
        pass

    obj = NotAHandle()
    assert not isinstance(obj, StreamingMessageHandle)
    assert not isinstance(obj, ToolEventHandle)


# === Conformance suite for concrete backends ===


class ViewBackendConformanceSuite:
    """Base class for concrete backend test classes.

    Concrete backends (REPLBackendTests, TelegramBackendTests, etc.)
    subclass this and provide a ``backend`` fixture yielding an
    instance. They inherit all tests defined here for free.

    Example:
        class TestREPLBackendConformance(ViewBackendConformanceSuite):
            @pytest.fixture
            async def backend(self, tmp_path):
                b = REPLBackend(config=test_config(tmp_path))
                await b.start()
                yield b
                await b.stop()
    """

    @pytest.fixture
    def backend(self):
        """Subclass must override with a real fixture."""
        pytest.skip(
            "ViewBackendConformanceSuite is abstract; subclass and "
            "override the backend fixture."
        )

    def test_is_view_backend_instance(self, backend):
        """The backend is an instance of ViewBackend."""
        assert isinstance(backend, ViewBackend)

    def test_has_all_abstract_methods_implemented(self, backend):
        """No abstract method leaks through — the backend class must
        have concrete implementations of all abstractmethods."""
        assert not getattr(
            type(backend), "__abstractmethods__", frozenset()
        ), (
            f"{type(backend).__name__} has unimplemented abstract methods: "
            f"{type(backend).__abstractmethods__}"
        )

    def test_set_input_handler_accepts_async_callable(self, backend):
        """set_input_handler stores the handler without raising."""
        async def fake_handler(text: str) -> None:
            pass
        backend.set_input_handler(fake_handler)  # should not raise

    def test_update_status_accepts_partial(self, backend):
        """update_status with a partial StatusUpdate doesn't crash."""
        backend.update_status(StatusUpdate(model="test-model"))
        backend.update_status(StatusUpdate(cost_usd=0.01))
        backend.update_status(StatusUpdate())  # empty partial

    def test_render_message_accepts_all_roles(self, backend):
        """render_message handles every Role without error."""
        for role in Role:
            backend.render_message(MessageEvent(role=role, content=f"test {role.value}"))

    def test_print_methods_accept_string(self, backend):
        """print_info/warning/error/panel accept strings without error."""
        backend.print_info("info line")
        backend.print_warning("warning line")
        backend.print_error("error line")
        backend.print_panel("panel body", title="panel title")
        backend.print_panel("panel without title")  # title is optional

    def test_request_exit_is_idempotent(self, backend):
        """request_exit must be safe to call multiple times."""
        backend.request_exit()
        backend.request_exit()  # second call must not raise

    def test_lifecycle_hooks_are_callable(self, backend):
        """Default no-op lifecycle hooks are safe to call."""
        backend.on_turn_start()
        backend.on_turn_end()
        backend.on_session_compaction(removed_tokens=100)
        backend.on_session_load(session_id="test", message_count=5)
        backend.voice_started()
        backend.voice_progress(seconds=1.0, peak=0.5)
        backend.voice_stopped(reason="manual_stop")
        backend.clear_screen()
        backend.mark_fatal_error(code="TEST", message="test error", retryable=True)

"""F5-wire-4: REPL _run_repl finally block calls runtime.shutdown()."""
from __future__ import annotations

import inspect



class TestReplTeardownInvokesShutdown:
    def test_source_mentions_runtime_shutdown(self) -> None:
        """Smoke check: _run_repl calls state.runtime.shutdown() inside
        the finally clause. We don't drive the whole TUI event loop here
        — just grep the function source for the wire. A full integration
        test would require a PT-in-pipe harness that this repo doesn't
        yet have."""
        from llm_code.cli.main import _run_repl

        src = inspect.getsource(_run_repl)
        assert "state.runtime.shutdown" in src, (
            "F5-wire-4 expects _run_repl to call state.runtime.shutdown() "
            "in its finally block."
        )

    def test_shutdown_is_guarded_against_exceptions(self) -> None:
        """Teardown must swallow errors so a broken shutdown can't
        stop the rest of the cleanup chain (backend.stop)."""
        from llm_code.cli.main import _run_repl

        src = inspect.getsource(_run_repl)
        # The shutdown call lives inside a try/except — we look for
        # the safety pattern rather than a precise string.
        shutdown_idx = src.find("state.runtime.shutdown")
        assert shutdown_idx > 0
        snippet = src[max(0, shutdown_idx - 150): shutdown_idx + 150]
        assert "try:" in snippet or "except" in snippet, (
            "runtime.shutdown should be wrapped in try/except so a "
            "failed teardown doesn't abort the rest of REPL shutdown."
        )

    def test_shutdown_precedes_backend_stop(self) -> None:
        """Sandbox teardown (may block on Docker) runs before backend
        teardown (prompt_toolkit cancels) so container cleanup isn't
        raced by event-loop exit. Compare the concrete call-sites —
        ``state.runtime.shutdown()`` and ``await backend.stop()`` —
        not upstream docstring mentions of ``backend.stop``."""
        from llm_code.cli.main import _run_repl

        src = inspect.getsource(_run_repl)
        shutdown_idx = src.find("state.runtime.shutdown()")
        stop_idx = src.find("await backend.stop()")
        assert shutdown_idx > 0 and stop_idx > 0
        assert shutdown_idx < stop_idx, (
            "runtime.shutdown should appear before backend.stop in the "
            "finally block so sandbox cleanup isn't interrupted."
        )


class TestShutdownHelperCallable:
    """If the source check passes but ConversationRuntime.shutdown is
    broken, we'd never notice from the REPL path. Keep a direct
    smoke test on the method itself."""

    def test_shutdown_is_callable_on_fresh_runtime_object(self) -> None:
        from types import SimpleNamespace

        from llm_code.runtime.conversation import ConversationRuntime

        rt = SimpleNamespace()
        # Bind ConversationRuntime.shutdown to a bare namespace — the
        # method only touches ``self._sandbox_lifecycle`` via
        # shutdown_sandbox_lifecycle, which tolerates an absent attr.
        ConversationRuntime.shutdown.__get__(rt)()

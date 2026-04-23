"""Tests for :mod:`llm_code.engine.async_component` (M5 — Task 5.2)."""
from __future__ import annotations

import asyncio

import pytest

from llm_code.engine.async_component import (
    async_component,
    ensure_run,
    ensure_run_async,
    is_async_native,
)
from llm_code.engine.component import component, output_types


# ---------------------------------------------------------------------------
# Sync-only component → auto-bridged run_async
# ---------------------------------------------------------------------------


@component
@output_types(value=int)
class _SyncOnly:
    def run(self, x: int) -> dict:
        return {"value": x + 1}


class TestSyncOnlyBridge:
    async def test_run_async_is_synthesised(self):
        c = _SyncOnly()
        result = await c.run_async(x=5)
        assert result == {"value": 6}

    def test_sync_run_still_works(self):
        c = _SyncOnly()
        assert c.run(x=5) == {"value": 6}

    async def test_run_async_bridge_marker_present(self):
        # The auto-generated async wrapper carries a sentinel attribute.
        assert getattr(_SyncOnly.run_async, "__run_async_is_bridge__", False)


# ---------------------------------------------------------------------------
# Async-only component → auto-bridged sync run
# ---------------------------------------------------------------------------


@component
@async_component
@output_types(value=int)
class _AsyncOnly:
    async def run_async(self, x: int) -> dict:
        await asyncio.sleep(0)
        return {"value": x * 2}


class TestAsyncOnlyBridge:
    async def test_run_async_direct(self):
        c = _AsyncOnly()
        assert await c.run_async(x=3) == {"value": 6}

    def test_sync_run_bridges_via_asyncio_run(self):
        c = _AsyncOnly()
        assert c.run(x=3) == {"value": 6}

    async def test_sync_run_in_running_loop_raises(self):
        c = _AsyncOnly()
        with pytest.raises(RuntimeError, match="running event loop"):
            c.run(x=3)

    def test_is_async_native_flag(self):
        assert is_async_native(_AsyncOnly)
        assert not is_async_native(_SyncOnly)


# ---------------------------------------------------------------------------
# Decoration-time validation
# ---------------------------------------------------------------------------


class TestAsyncComponentValidation:
    def test_missing_run_async_raises(self):
        with pytest.raises(TypeError, match="requires"):
            @async_component
            class _Bad:
                def run(self, x):
                    return {}

    def test_non_async_run_async_raises(self):
        with pytest.raises(TypeError, match="`async def`"):
            @async_component
            class _Bad:
                def run_async(self, x):  # not async!
                    return {}

    def test_cannot_define_both_run_and_run_async(self):
        with pytest.raises(TypeError, match="cannot define both"):
            @async_component
            class _Bad:
                def run(self, x):
                    return {}

                async def run_async(self, x):
                    return {}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_double_decoration_is_safe(self):
        @component
        @output_types(value=int)
        class _X:
            def run(self, x: int) -> dict:
                return {"value": x}

        # Re-applying component() should not overwrite the existing bridge.
        X = component(_X)
        assert await X().run_async(x=7) == {"value": 7}

    def test_ensure_run_async_no_op_when_async_exists(self):
        class _Y:
            async def run_async(self, x):
                return {"ok": x}

            def run(self, x):
                return {"ok": x}

        existing = _Y.run_async
        ensure_run_async(_Y)
        # Still the original method — no bridge substitution.
        assert _Y.run_async is existing

    def test_ensure_run_no_op_when_sync_exists(self):
        class _Z:
            def run(self, x):
                return {"ok": x}

        existing = _Z.run
        ensure_run(_Z)
        assert _Z.run is existing

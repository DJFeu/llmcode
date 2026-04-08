"""_run_async must work both inside and outside an existing event loop."""
from __future__ import annotations

import pytest

from llm_code.lsp.tools import _run_async


async def _square(x: int) -> int:
    return x * x


def test_run_async_outside_loop() -> None:
    assert _run_async(_square(7)) == 49


@pytest.mark.asyncio
async def test_run_async_inside_loop() -> None:
    """Calling from within an existing loop must offload and not deadlock."""
    assert _run_async(_square(8)) == 64

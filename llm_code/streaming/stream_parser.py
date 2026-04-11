"""Backward-compatibility shim — canonical module is now
``llm_code.view.stream_parser``.

Originally pointed at ``llm_code.tui.stream_parser``; M11 cutover
relocated the canonical copy into ``view/`` and deleted the tui/
package. This shim is kept so any out-of-tree consumers that imported
``llm_code.streaming.stream_parser`` keep working.
"""
from llm_code.view.stream_parser import (  # noqa: F401
    StreamEvent,
    StreamEventKind,
    StreamParser,
)

"""StreamParser behaviour for variant 7 Harmony blocks (v2.2.5).

v2.2.4 introduced ``</arg_value>`` as an alternative close tag for
variant 6. That worked for variant 6 but broke variant 7 streams
whose body legitimately contains ``</arg_value>`` tags at every
pair. v2.2.5 restores ``</tool_call>`` priority so variant 7 blocks
close on their real end tag.

v13 Phase C note: variant-6/7 support is profile-driven now. Every
test builds a parser via ``_glm_parser()`` below to inject the same
hints the ``65-glm-5.1.toml`` profile declares in ``[parser_hints]``.
"""
from __future__ import annotations

from llm_code.view.stream_parser import StreamEventKind, StreamParser

_GLM_CUSTOM_CLOSE = ("</arg_value>",)
_GLM_CALL_SEPARATOR = "\u2192 \t\r\n"
_GLM_STANDARD_CLOSE_REQUIRED_ON = ("<arg_key>",)


def _glm_parser() -> StreamParser:
    """Build a ``StreamParser`` with the GLM-5.1 parser hints."""
    return StreamParser(
        custom_close_tags=_GLM_CUSTOM_CLOSE,
        call_separator_chars=_GLM_CALL_SEPARATOR,
        standard_close_required_on=_GLM_STANDARD_CLOSE_REQUIRED_ON,
    )


def _fire(parser: StreamParser, *chunks: str, flush: bool = True) -> list:
    events: list = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    if flush:
        events.extend(parser.flush())
    return events


class TestStandardCloseBeatsArgValue:
    def test_variant_7_single_arg(self) -> None:
        """The body has ``<arg_value>…</arg_value>`` but must not
        be mistaken for variant 6 close — the real close is
        ``</tool_call>`` at the end."""
        p = _glm_parser()
        events = _fire(
            p,
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>\n"
            "</tool_call>",
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        call = tool_events[0].tool_call
        assert call is not None
        assert call.name == "web_search"
        assert call.args == {"query": "news"}

    def test_variant_7_multi_arg_inner_arg_value_not_eaten(self) -> None:
        """Three ``<arg_value>`` inside the body — all three must
        survive until the real ``</tool_call>`` closes."""
        p = _glm_parser()
        events = _fire(
            p,
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>news</arg_value>\n"
            "<arg_key>max_results</arg_key>\n"
            "<arg_value>5</arg_value>\n"
            "<arg_key>lang</arg_key>\n"
            "<arg_value>zh</arg_value>\n"
            "</tool_call>",
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        call = tool_events[0].tool_call
        assert call.args == {"query": "news", "max_results": 5, "lang": "zh"}

    def test_variant_7_chunked_streaming(self) -> None:
        """The close and sibling pairs land in different chunks —
        parser must hold state until the real end."""
        p = _glm_parser()
        events: list = []
        for chunk in (
            "<tool_call>\nweb_search\n",
            "<arg_key>query</arg_key>\n",
            "<arg_value>news</arg_value>\n",
            "<arg_key>max_results</arg_key>\n",
            "<arg_value>5</arg_value>\n",
            "</tool_call>",
        ):
            events.extend(p.feed(chunk))
        events.extend(p.flush())
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.args == {"query": "news", "max_results": 5}


class TestVariant6StillWorks:
    """Regression guard — variant 6 (``</arg_value>`` as the ONLY
    close tag) must still parse on streams that never emit
    ``</tool_call>`` at all."""

    def test_variant_6_single_call_unchanged(self) -> None:
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>',
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.args == {"query": "a"}

    def test_variant_6_arrow_chain_unchanged(self) -> None:
        p = _glm_parser()
        events = _fire(
            p,
            '<tool_call>web_search}{"query":"a"}</arg_value>'
            "→"
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 2


class TestMixedFormats:
    def test_variant_7_then_variant_6_in_same_stream(self) -> None:
        """Adversarial: a well-formed variant 7 block followed by
        a variant 6 block. The variant 7 block MUST close on
        ``</tool_call>``, the variant 6 block on ``</arg_value>``."""
        p = _glm_parser()
        events = _fire(
            p,
            "<tool_call>\n"
            "web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>first</arg_value>\n"
            "</tool_call>\n"
            '<tool_call>web_search}{"query":"second"}</arg_value>',
        )
        tool_events = [e for e in events if e.kind == StreamEventKind.TOOL_CALL]
        assert len(tool_events) == 2
        assert tool_events[0].tool_call.args == {"query": "first"}
        assert tool_events[1].tool_call.args == {"query": "second"}

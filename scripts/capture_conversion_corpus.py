"""Capture before-refactor conversion output for v15 M3 parity gate.

Walks ~50 representative ``MessageRequest`` scenarios (single-turn,
multi-turn, with tools, with reasoning, with images, with cache_control,
with mixed-content edges) and dumps both providers' converted dict[]
to JSON.

Run BEFORE the v15 M3 conversion-layer refactor; commit the JSON.
Re-run AFTER refactor and assert equality. Any byte difference means
the conversion was not behaviour-preserving.

Usage::

    .venv/bin/python scripts/capture_conversion_corpus.py

Output: ``tests/fixtures/conversion_corpus.json``.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Make the project importable when run from the repo root.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from llm_code.api.anthropic_provider import AnthropicProvider  # noqa: E402
from llm_code.api.openai_compat import OpenAICompatProvider  # noqa: E402
from llm_code.api.types import (  # noqa: E402
    ImageBlock,
    Message,
    ServerToolResultBlock,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


@dataclass
class Scenario:
    """One labelled conversion scenario.

    ``input_messages`` is a serialisable representation of the input
    ``tuple[Message, ...]`` (each block is a tagged dict). ``input_*``
    fields capture the rest of the request context (system, profile
    flags) that may affect conversion.
    """
    name: str
    input_messages: list[dict[str, Any]]
    input_system: str | None = None
    strip_prior_reasoning: bool = False  # OpenAI compat profile knob
    expected_anthropic: list[dict[str, Any]] = field(default_factory=list)
    expected_openai: list[dict[str, Any]] = field(default_factory=list)


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Tag-encode a content block so JSON survives round-trip."""
    if isinstance(block, TextBlock):
        return {"_type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"_type": "thinking", "content": block.content,
                "signature": block.signature}
    if isinstance(block, ToolUseBlock):
        return {"_type": "tool_use", "id": block.id, "name": block.name,
                "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {"_type": "tool_result", "tool_use_id": block.tool_use_id,
                "content": block.content, "is_error": block.is_error}
    if isinstance(block, ImageBlock):
        return {"_type": "image", "media_type": block.media_type,
                "data": block.data}
    if isinstance(block, ServerToolUseBlock):
        return {"_type": "server_tool_use", "id": block.id,
                "name": block.name, "input": block.input,
                "signature": block.signature}
    if isinstance(block, ServerToolResultBlock):
        return {"_type": "server_tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content, "signature": block.signature}
    raise ValueError(f"unhandled block: {type(block).__name__}")


def _msg_to_dict(msg: Message) -> dict[str, Any]:
    return {
        "role": msg.role,
        "content": [_block_to_dict(b) for b in msg.content],
    }


def _dict_to_block(data: dict[str, Any]) -> Any:
    t = data["_type"]
    if t == "text":
        return TextBlock(text=data["text"])
    if t == "thinking":
        return ThinkingBlock(content=data["content"], signature=data["signature"])
    if t == "tool_use":
        return ToolUseBlock(id=data["id"], name=data["name"], input=data["input"])
    if t == "tool_result":
        return ToolResultBlock(
            tool_use_id=data["tool_use_id"],
            content=data["content"],
            is_error=data["is_error"],
        )
    if t == "image":
        return ImageBlock(media_type=data["media_type"], data=data["data"])
    if t == "server_tool_use":
        return ServerToolUseBlock(
            id=data["id"], name=data["name"], input=data["input"],
            signature=data["signature"],
        )
    if t == "server_tool_result":
        return ServerToolResultBlock(
            tool_use_id=data["tool_use_id"],
            content=data["content"],
            signature=data["signature"],
        )
    raise ValueError(f"unhandled tagged block: {t}")


def _dict_to_msg(data: dict[str, Any]) -> Message:
    return Message(
        role=data["role"],
        content=tuple(_dict_to_block(b) for b in data["content"]),
    )


# ── Scenario builders ────────────────────────────────────────────────


def _user_text(text: str) -> Message:
    return Message(role="user", content=(TextBlock(text=text),))


def _assistant_text(text: str) -> Message:
    return Message(role="assistant", content=(TextBlock(text=text),))


def _tool_result(tool_id: str, body: str, is_error: bool = False) -> Message:
    return Message(
        role="user",
        content=(ToolResultBlock(
            tool_use_id=tool_id, content=body, is_error=is_error,
        ),),
    )


def _tool_use_msg(tool_id: str, name: str, args: dict) -> Message:
    return Message(
        role="assistant",
        content=(ToolUseBlock(id=tool_id, name=name, input=args),),
    )


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []

    # 1. Single-turn user.
    scenarios.append(Scenario(
        name="single_turn_user",
        input_messages=[_msg_to_dict(_user_text("Hello"))],
    ))

    # 2. Single-turn user + assistant.
    scenarios.append(Scenario(
        name="single_turn_pair",
        input_messages=[
            _msg_to_dict(_user_text("Hello")),
            _msg_to_dict(_assistant_text("Hi there!")),
        ],
    ))

    # 3. Multi-turn (5 messages).
    scenarios.append(Scenario(
        name="multi_turn_5",
        input_messages=[
            _msg_to_dict(_user_text("What is 2+2?")),
            _msg_to_dict(_assistant_text("4")),
            _msg_to_dict(_user_text("And 3+3?")),
            _msg_to_dict(_assistant_text("6")),
            _msg_to_dict(_user_text("Now 10+10?")),
        ],
    ))

    # 4. Multi-turn (10 messages with system).
    msgs10 = []
    for i in range(5):
        msgs10.append(_msg_to_dict(_user_text(f"Q{i}")))
        msgs10.append(_msg_to_dict(_assistant_text(f"A{i}")))
    scenarios.append(Scenario(
        name="multi_turn_10_with_system",
        input_messages=msgs10,
        input_system="You are a helpful assistant.",
    ))

    # 5. Tool use single call.
    scenarios.append(Scenario(
        name="tool_use_single",
        input_messages=[
            _msg_to_dict(_user_text("Search for x")),
            _msg_to_dict(_tool_use_msg("call_1", "web_search", {"query": "x"})),
            _msg_to_dict(_tool_result("call_1", "result body")),
            _msg_to_dict(_assistant_text("Done.")),
        ],
    ))

    # 6. Tool use with multiple tools sequentially.
    scenarios.append(Scenario(
        name="tool_use_sequence",
        input_messages=[
            _msg_to_dict(_user_text("Multi step")),
            _msg_to_dict(_tool_use_msg("a", "read_file", {"path": "x"})),
            _msg_to_dict(_tool_result("a", "content of x")),
            _msg_to_dict(_tool_use_msg("b", "write_file", {"path": "y", "text": "z"})),
            _msg_to_dict(_tool_result("b", "ok")),
            _msg_to_dict(_assistant_text("Both files handled.")),
        ],
    ))

    # 7. Tool result with error.
    scenarios.append(Scenario(
        name="tool_result_error",
        input_messages=[
            _msg_to_dict(_user_text("Run x")),
            _msg_to_dict(_tool_use_msg("c", "bash", {"command": "x"})),
            _msg_to_dict(Message(
                role="user",
                content=(ToolResultBlock(
                    tool_use_id="c", content="permission denied", is_error=True,
                ),),
            )),
        ],
    ))

    # 8. Multiple tool_use blocks in one assistant message.
    scenarios.append(Scenario(
        name="multi_tool_use_one_msg",
        input_messages=[
            _msg_to_dict(_user_text("Read both")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="t1", name="read_file", input={"path": "a"}),
                    ToolUseBlock(id="t2", name="read_file", input={"path": "b"}),
                ),
            )),
            _msg_to_dict(Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="t1", content="A"),
                    ToolResultBlock(tool_use_id="t2", content="B"),
                ),
            )),
        ],
    ))

    # 9. Thinking block in assistant message.
    scenarios.append(Scenario(
        name="thinking_block_simple",
        input_messages=[
            _msg_to_dict(_user_text("Think hard")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(
                        content="Let me work this out step by step.",
                        signature="sig123",
                    ),
                    TextBlock(text="The answer is 42."),
                ),
            )),
        ],
    ))

    # 10. Thinking + tool_use in one assistant message.
    scenarios.append(Scenario(
        name="thinking_plus_tool_use",
        input_messages=[
            _msg_to_dict(_user_text("Search and reason")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="I should search for x", signature="s"),
                    ToolUseBlock(id="th1", name="web_search", input={"query": "x"}),
                ),
            )),
            _msg_to_dict(_tool_result("th1", "found x")),
        ],
    ))

    # 11. Image in user message.
    scenarios.append(Scenario(
        name="image_user_msg",
        input_messages=[
            _msg_to_dict(Message(
                role="user",
                content=(
                    TextBlock(text="What is this?"),
                    ImageBlock(media_type="image/png", data="base64data=="),
                ),
            )),
        ],
    ))

    # 12. Mixed image + text + tool_use (assistant) — Anthropic-only.
    scenarios.append(Scenario(
        name="mixed_text_image_user",
        input_messages=[
            _msg_to_dict(Message(
                role="user",
                content=(
                    TextBlock(text="Look at these"),
                    ImageBlock(media_type="image/jpeg", data="img1=="),
                    TextBlock(text="And this"),
                    ImageBlock(media_type="image/png", data="img2=="),
                ),
            )),
        ],
    ))

    # 13. System prompt only (no messages).
    # Skip — every conversation must have at least one user message.

    # 14. Empty content (assistant with empty TextBlock).
    scenarios.append(Scenario(
        name="empty_assistant_text",
        input_messages=[
            _msg_to_dict(_user_text("Hi")),
            _msg_to_dict(Message(
                role="assistant",
                content=(TextBlock(text=""),),
            )),
        ],
    ))

    # 15. Tool result content as JSON-stringified dict.
    scenarios.append(Scenario(
        name="tool_result_json_string",
        input_messages=[
            _msg_to_dict(_user_text("List files")),
            _msg_to_dict(_tool_use_msg("ls1", "list_files", {"path": "/tmp"})),
            _msg_to_dict(_tool_result(
                "ls1", '{"files": ["a.txt", "b.txt"], "count": 2}',
            )),
        ],
    ))

    # 16. Long content (1000+ chars).
    long_text = "x" * 5000
    scenarios.append(Scenario(
        name="long_content",
        input_messages=[
            _msg_to_dict(_user_text(long_text)),
            _msg_to_dict(_assistant_text(long_text)),
        ],
    ))

    # 17. Unicode + emoji.
    scenarios.append(Scenario(
        name="unicode_emoji",
        input_messages=[
            _msg_to_dict(_user_text("こんにちは 你好 🌍")),
            _msg_to_dict(_assistant_text("Hi! 👋")),
        ],
    ))

    # 18. Server tool use (Anthropic web search).
    scenarios.append(Scenario(
        name="server_tool_use_block",
        input_messages=[
            _msg_to_dict(_user_text("Search for news")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ServerToolUseBlock(
                        id="srv1", name="web_search",
                        input={"query": "news"},
                        signature="srvsig",
                    ),
                ),
            )),
        ],
    ))

    # 19. Server tool result.
    scenarios.append(Scenario(
        name="server_tool_result_block",
        input_messages=[
            _msg_to_dict(_user_text("Search")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ServerToolUseBlock(
                        id="srv2", name="web_search",
                        input={"q": "x"}, signature="a",
                    ),
                    ServerToolResultBlock(
                        tool_use_id="srv2",
                        content="Headlines: foo, bar",
                        signature="b",
                    ),
                ),
            )),
        ],
    ))

    # 20. Tool use with empty args.
    scenarios.append(Scenario(
        name="tool_use_empty_args",
        input_messages=[
            _msg_to_dict(_user_text("Get time")),
            _msg_to_dict(_tool_use_msg("now1", "current_time", {})),
            _msg_to_dict(_tool_result("now1", "12:00")),
        ],
    ))

    # 21. Tool use with nested args.
    scenarios.append(Scenario(
        name="tool_use_nested_args",
        input_messages=[
            _msg_to_dict(_user_text("Configure")),
            _msg_to_dict(_tool_use_msg("cfg1", "configure", {
                "settings": {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}},
                "flags": ["x", "y"],
            })),
            _msg_to_dict(_tool_result("cfg1", "ok")),
        ],
    ))

    # 22. Multiple consecutive tool results.
    scenarios.append(Scenario(
        name="consecutive_tool_results",
        input_messages=[
            _msg_to_dict(_user_text("Many tools")),
            _msg_to_dict(_tool_use_msg("p1", "f", {})),
            _msg_to_dict(_tool_result("p1", "r1")),
            _msg_to_dict(_tool_use_msg("p2", "f", {})),
            _msg_to_dict(_tool_result("p2", "r2")),
            _msg_to_dict(_tool_use_msg("p3", "f", {})),
            _msg_to_dict(_tool_result("p3", "r3")),
        ],
    ))

    # 23. Empty tool_result content.
    scenarios.append(Scenario(
        name="empty_tool_result",
        input_messages=[
            _msg_to_dict(_user_text("X")),
            _msg_to_dict(_tool_use_msg("e1", "f", {})),
            _msg_to_dict(_tool_result("e1", "")),
        ],
    ))

    # 24. Multi-block assistant: text + thinking + text.
    scenarios.append(Scenario(
        name="multi_block_assistant_text_thinking_text",
        input_messages=[
            _msg_to_dict(_user_text("X")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="reasoning", signature="s"),
                    TextBlock(text="answer"),
                ),
            )),
        ],
    ))

    # 25. Strip-prior-reasoning enabled (v14 mech B carry-through).
    # Not visible at this layer of the code (we test the openai compat
    # path with assistant messages whose dict includes reasoning_content
    # — at that level, _convert_message returns a dict; the strip
    # filter runs at _build_messages time. Same input message tree;
    # the difference is the profile flag.
    scenarios.append(Scenario(
        name="strip_prior_reasoning_flag",
        input_messages=[
            _msg_to_dict(_user_text("Q")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="reasoning_text", signature=""),
                    TextBlock(text="answer"),
                ),
            )),
            _msg_to_dict(_user_text("Followup")),
        ],
        strip_prior_reasoning=True,
    ))

    # 26-30. More mixed coverage.
    scenarios.append(Scenario(
        name="thinking_only_no_text",
        input_messages=[
            _msg_to_dict(_user_text("Think")),
            _msg_to_dict(Message(
                role="assistant",
                content=(ThinkingBlock(content="just thinking", signature=""),),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="single_assistant_text",
        input_messages=[_msg_to_dict(_assistant_text("standalone"))],
    ))
    scenarios.append(Scenario(
        name="user_with_only_image",
        input_messages=[
            _msg_to_dict(Message(
                role="user",
                content=(ImageBlock(media_type="image/webp", data="webp=="),),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="tool_result_unicode",
        input_messages=[
            _msg_to_dict(_user_text("查詢")),
            _msg_to_dict(_tool_use_msg("u1", "search", {"q": "新聞"})),
            _msg_to_dict(_tool_result("u1", "今日熱門新聞:...")),
        ],
    ))
    scenarios.append(Scenario(
        name="tool_use_with_special_chars",
        input_messages=[
            _msg_to_dict(_user_text("Run")),
            _msg_to_dict(_tool_use_msg("sc1", "bash", {
                "command": "echo \"hello\\nworld\"",
            })),
            _msg_to_dict(_tool_result("sc1", "hello\nworld")),
        ],
    ))

    # 31-40: edge cases the production stream sees.
    scenarios.append(Scenario(
        name="thinking_unsigned",
        input_messages=[
            _msg_to_dict(_user_text("Think")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="local reasoning", signature=""),
                    TextBlock(text="result"),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="back_to_back_thinking_blocks",
        input_messages=[
            _msg_to_dict(_user_text("Q")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="step 1", signature="a"),
                    ThinkingBlock(content="step 2", signature="b"),
                    TextBlock(text="answer"),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="conversation_with_system",
        input_messages=[
            _msg_to_dict(_user_text("Hi")),
            _msg_to_dict(_assistant_text("Hello!")),
            _msg_to_dict(_user_text("What's up")),
        ],
        input_system="Be concise.",
    ))
    scenarios.append(Scenario(
        name="long_system_prompt",
        input_messages=[_msg_to_dict(_user_text("Q"))],
        input_system="x" * 2000,
    ))
    scenarios.append(Scenario(
        name="tool_result_after_user_text",
        # User message that contains BOTH text and a tool result block.
        input_messages=[
            _msg_to_dict(_user_text("Initial")),
            _msg_to_dict(_tool_use_msg("ar1", "f", {})),
            _msg_to_dict(Message(
                role="user",
                content=(
                    TextBlock(text="extra context"),
                    ToolResultBlock(tool_use_id="ar1", content="r"),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="parallel_tool_calls_one_assistant",
        input_messages=[
            _msg_to_dict(_user_text("Read all")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="px1", name="read_file", input={"p": "1"}),
                    ToolUseBlock(id="px2", name="read_file", input={"p": "2"}),
                    ToolUseBlock(id="px3", name="read_file", input={"p": "3"}),
                ),
            )),
            _msg_to_dict(Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="px1", content="A"),
                    ToolResultBlock(tool_use_id="px2", content="B"),
                    ToolResultBlock(tool_use_id="px3", content="C"),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="server_tool_pair_with_text",
        input_messages=[
            _msg_to_dict(_user_text("Search")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    TextBlock(text="Let me search."),
                    ServerToolUseBlock(
                        id="s1", name="web_search",
                        input={"q": "x"}, signature="sig1",
                    ),
                    ServerToolResultBlock(
                        tool_use_id="s1", content="Result text", signature="sig2",
                    ),
                    TextBlock(text="Found it."),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="three_turn_with_tool_loop",
        input_messages=[
            _msg_to_dict(_user_text("Loop")),
            _msg_to_dict(_tool_use_msg("l1", "step", {"i": 1})),
            _msg_to_dict(_tool_result("l1", "1")),
            _msg_to_dict(_tool_use_msg("l2", "step", {"i": 2})),
            _msg_to_dict(_tool_result("l2", "2")),
            _msg_to_dict(_tool_use_msg("l3", "step", {"i": 3})),
            _msg_to_dict(_tool_result("l3", "3")),
            _msg_to_dict(_assistant_text("Done.")),
        ],
    ))
    scenarios.append(Scenario(
        name="empty_text_in_user",
        input_messages=[
            _msg_to_dict(Message(
                role="user",
                content=(TextBlock(text=""),),
            )),
            _msg_to_dict(_assistant_text("ok")),
        ],
    ))
    scenarios.append(Scenario(
        name="user_text_then_tool_result",
        # Anthropic-shape: tool result on its own user message.
        input_messages=[
            _msg_to_dict(_user_text("Initial")),
            _msg_to_dict(_assistant_text("Processing...")),
            _msg_to_dict(_tool_use_msg("u1", "f", {})),
            _msg_to_dict(_tool_result("u1", "result")),
        ],
    ))

    # 41-50: more edges and tool variations.
    scenarios.append(Scenario(
        name="many_short_turns",
        input_messages=[
            _msg_to_dict(_user_text("a")),
            _msg_to_dict(_assistant_text("b")),
            _msg_to_dict(_user_text("c")),
            _msg_to_dict(_assistant_text("d")),
            _msg_to_dict(_user_text("e")),
            _msg_to_dict(_assistant_text("f")),
            _msg_to_dict(_user_text("g")),
            _msg_to_dict(_assistant_text("h")),
        ],
    ))
    scenarios.append(Scenario(
        name="tool_use_then_text_assistant",
        input_messages=[
            _msg_to_dict(_user_text("Search and answer")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    TextBlock(text="Let me search:"),
                    ToolUseBlock(id="x1", name="web_search", input={"q": "y"}),
                ),
            )),
            _msg_to_dict(_tool_result("x1", "found")),
            _msg_to_dict(_assistant_text("Result is...")),
        ],
    ))
    scenarios.append(Scenario(
        name="tool_use_args_with_none_value",
        input_messages=[
            _msg_to_dict(_user_text("Configure")),
            _msg_to_dict(_tool_use_msg("n1", "configure", {
                "key": None, "value": "x", "ints": [1, 2]
            })),
            _msg_to_dict(_tool_result("n1", "ok")),
        ],
    ))
    scenarios.append(Scenario(
        name="long_tool_result",
        input_messages=[
            _msg_to_dict(_user_text("Read big file")),
            _msg_to_dict(_tool_use_msg("big1", "read_file", {"path": "/a"})),
            _msg_to_dict(_tool_result("big1", "x" * 10000)),
        ],
    ))
    scenarios.append(Scenario(
        name="single_tool_use_no_args",
        input_messages=[
            _msg_to_dict(_user_text("Hi")),
            _msg_to_dict(_tool_use_msg("t1", "ping", {})),
            _msg_to_dict(_tool_result("t1", "pong")),
        ],
    ))
    scenarios.append(Scenario(
        name="two_thinking_one_tool",
        input_messages=[
            _msg_to_dict(_user_text("Q")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="step a", signature="x"),
                    ThinkingBlock(content="step b", signature="y"),
                    ToolUseBlock(id="t1", name="search", input={"q": "z"}),
                ),
            )),
            _msg_to_dict(_tool_result("t1", "answer")),
        ],
    ))
    scenarios.append(Scenario(
        name="zero_messages_with_system",
        input_messages=[],
        input_system="You are helpful.",
    ))
    scenarios.append(Scenario(
        name="image_assistant_msg",
        # Edge: an image in an assistant message (rare; some providers
        # forbid this — we capture the actual current behaviour).
        input_messages=[
            _msg_to_dict(_user_text("Make image")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    TextBlock(text="Here:"),
                    ImageBlock(media_type="image/png", data="genimg=="),
                ),
            )),
        ],
    ))
    scenarios.append(Scenario(
        name="tool_use_unicode_args",
        input_messages=[
            _msg_to_dict(_user_text("Search")),
            _msg_to_dict(_tool_use_msg("u1", "search", {
                "q": "今日熱門新聞 🔥",
                "lang": "zh-TW",
            })),
            _msg_to_dict(_tool_result("u1", "results: ...")),
        ],
    ))
    scenarios.append(Scenario(
        name="all_blocks_one_assistant",
        # Stress: thinking + text + tool_use + text — every block type
        # an assistant can emit in one message.
        input_messages=[
            _msg_to_dict(_user_text("Maximum")),
            _msg_to_dict(Message(
                role="assistant",
                content=(
                    ThinkingBlock(content="my chain", signature="cs"),
                    TextBlock(text="Reasoning aloud"),
                    ToolUseBlock(id="ax1", name="f", input={"x": 1}),
                    TextBlock(text="Now I'll process the result"),
                ),
            )),
            _msg_to_dict(_tool_result("ax1", "ok")),
        ],
    ))

    return scenarios


def capture_scenario(scenario: Scenario) -> Scenario:
    """Run both providers' converters on a scenario, populating
    ``expected_anthropic`` and ``expected_openai`` fields.
    """
    messages = tuple(
        _dict_to_msg(m) for m in scenario.input_messages
    )

    # Anthropic — uses the existing _build_messages.
    a_provider = AnthropicProvider(api_key="x", model_name="claude-sonnet-4-6")
    try:
        scenario.expected_anthropic = a_provider._build_messages(messages)
    finally:
        # close synchronously
        import asyncio
        asyncio.get_event_loop().run_until_complete(a_provider.close())

    # OpenAI compat — uses _build_messages(messages, system=...).
    # Use a profile that flips strip_prior_reasoning when the scenario
    # asks for it; otherwise default profile.
    o_provider = OpenAICompatProvider(
        base_url="http://example.com",
        api_key="x",
        model_name="default",
    )
    try:
        if scenario.strip_prior_reasoning:
            from dataclasses import replace
            o_provider._profile = replace(
                o_provider._profile, strip_prior_reasoning=True,
            )
        scenario.expected_openai = o_provider._build_messages(
            messages, system=scenario.input_system,
        )
    finally:
        import asyncio
        asyncio.get_event_loop().run_until_complete(o_provider.close())

    return scenario


def main() -> None:
    out = _REPO / "tests" / "fixtures" / "conversion_corpus.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    scenarios = build_scenarios()
    captured = [capture_scenario(s) for s in scenarios]

    payload = [asdict(s) for s in captured]
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Captured {len(captured)} scenarios → {out.relative_to(_REPO)}")


if __name__ == "__main__":
    main()

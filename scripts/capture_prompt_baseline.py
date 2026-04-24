"""Capture prompt routing / parser / stream-parser behaviour as a
golden fixture — used by Phase B of the v13 migration to lock in
the pre-migration behaviour and verify Phase B didn't regress any
shipped model id.

Phase C removed the parity test suites that consumed these snapshots
(mainline tests now cover the same paths). The script is kept for
future migrations: if a similar cleanup ever needs a byte-for-byte
baseline, run this first, rename the outputs to ``pre_v14_*``, and
repurpose as needed.

Usage:
    .venv/bin/python scripts/capture_prompt_baseline.py

The three snapshots land under ``tests/fixtures/``.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

# Suppress the Phase A deprecation warning so the baseline capture runs
# silently — the warning is intentional but noisy when sweeping ~30 ids.
warnings.simplefilter("ignore", DeprecationWarning)

from llm_code.runtime.prompt import select_intro_prompt  # noqa: E402
from llm_code.tools.parsing import parse_tool_calls  # noqa: E402
from llm_code.view.stream_parser import StreamParser  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_FIXTURES = _REPO / "tests" / "fixtures"


# ── Prompt routing ──────────────────────────────────────────────────────

# Every model id mentioned in the legacy if-ladder, plus a handful of
# common real-world ids that exercise the substring-match boundaries.
MODEL_IDS = [
    # GLM (variant 6/7 home turf — must keep its tuned prompt + parser)
    "glm-5.1", "glm-4-plus", "zhipu-chatglm", "GLM-5.1",
    # Qwen (vLLM XML fallback path)
    "qwen3.5-plus", "qwen2.5-coder", "qwen-vl-plus", "qwen3.5-122b",
    # DeepSeek
    "deepseek-v3", "deepseek-r1", "deepseek-chat",
    # Anthropic family
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
    "anthropic/claude-sonnet-4",
    # Gemini
    "gemini-2.5-flash", "gemini-2.0-pro",
    # Llama
    "llama-3.3-70b", "llama-3.1",
    # Kimi / Moonshot
    "kimi-k2", "moonshot-v1",
    # GPT family — exercises the beast-vs-gpt boundary
    "gpt-5", "gpt-5-turbo", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo",
    "o1", "o1-preview", "o3-mini",
    # Wrapped backends
    "trinity-alpha", "copilot-gpt-5", "copilot-gpt5", "codex-mini",
    # Beast-only words (no copilot, no gpt-prefix)
    "beast-qwen",
    # Empty + unknown — fall through to default
    "", "some-new-model-nobody-heard-of",
]


def _capture_prompts() -> dict[str, str]:
    return {mid: select_intro_prompt(mid) for mid in MODEL_IDS}


# ── Parser corpus ───────────────────────────────────────────────────────

# Cover every variant in DEFAULT_VARIANT_ORDER:
#   1. json_payload
#   2. hermes_function (full + truncated)
#   3. hermes_truncated
#   4. harmony_kv (variant 7)
#   5. glm_brace (variant 6)
#   6. bare_name_tag (variant 5)
PARSER_CORPUS: list[tuple[str, str]] = [
    # JSON payload
    (
        "json_simple",
        '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>',
    ),
    (
        "json_no_args",
        '<tool_call>{"tool": "task_close"}</tool_call>',
    ),
    # Hermes full form
    (
        "hermes_full_basic",
        "<tool_call><function=read_file>"
        "<parameter=path>/tmp/foo</parameter>"
        "</function></tool_call>",
    ),
    (
        "hermes_full_multi_param",
        "<tool_call><function=edit_file>"
        "<parameter=path>/x.py</parameter>"
        "<parameter=old_string>a</parameter>"
        "<parameter=new_string>b</parameter>"
        "</function></tool_call>",
    ),
    # Hermes truncated form
    (
        "hermes_truncated_param_blocks",
        "<tool_call>web_search>"
        "<parameter=query>news</parameter>"
        "</tool_call>",
    ),
    (
        "hermes_truncated_json_args",
        '<tool_call>web_search>{"query": "news", "max_results": 3}</tool_call>',
    ),
    (
        "hermes_truncated_brace_no_gt",
        '<tool_call>bash{"command": "ls"}</tool_call>',
    ),
    # Harmony / variant 7 — <arg_key>/<arg_value> body
    (
        "harmony_simple",
        "<tool_call>web_search\n"
        "<arg_key>query</arg_key><arg_value>news</arg_value>\n"
        "<arg_key>max_results</arg_key><arg_value>5</arg_value>\n"
        "</tool_call>",
    ),
    # GLM variant 6 — NAME}{JSON}</arg_value> body
    (
        "glm_variant_simple",
        '<tool_call>web_search}{"query":"news","max_results":3}</arg_value>',
    ),
    (
        "glm_variant_arrow_chain",
        '<tool_call>web_search}{"query":"a"}</arg_value>'
        "\u2192"
        '<tool_call>web_search}{"query":"b"}</arg_value>',
    ),
    # Bare name-as-tag — variant 5, wrapper-less
    (
        "bare_name_simple",
        '<web_search>{"query": "news"}</web_search>',
    ),
    (
        "bare_name_truncated_close",
        '<web_search>{"query": "news"}</search>',
    ),
    # Mixed corpus: text around the call
    (
        "json_with_prose",
        'Sure, let me check.\n'
        '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>\n'
        'Done.',
    ),
    (
        "two_json_calls",
        '<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'
        '<tool_call>{"tool": "bash", "args": {"command": "pwd"}}</tool_call>',
    ),
    # Edge cases that must still be NO PARSE
    ("empty", ""),
    ("plain_text", "just answering directly without tools"),
    ("only_open_tag", "<tool_call>"),
    ("malformed_json", '<tool_call>{"tool": broken}</tool_call>'),
    ("unknown_format", "<tool_call>weirdo://payload</tool_call>"),
    # Native-style probe (no XML — the function returns the native list
    # directly so this exercises a different branch but produces the
    # same shape for snapshot purposes)
    ("nothing_visible", "<thought>just thinking</thought>"),
]


def _serialize_parsed(parsed_list) -> list[dict]:
    """Convert a list of ``ParsedToolCall`` to plain dicts.

    ``id`` is stripped because each parse generates a fresh UUID — the
    parity assertion is on (name, args, source), not on the random id.
    """
    return [
        {"name": p.name, "args": p.args, "source": p.source}
        for p in parsed_list
    ]


def _capture_parser() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for label, body in PARSER_CORPUS:
        # profile=None → DEFAULT_VARIANT_ORDER (the historical sequence)
        parsed = parse_tool_calls(body, None, profile=None)
        out[label] = _serialize_parsed(parsed)
    return out


# ── Stream parser corpus ───────────────────────────────────────────────

# Each entry is a list of chunks fed to a fresh ``StreamParser()`` (with
# v2.2.5 defaults — implicitly the GLM-friendly ones). Snapshot is the
# sequence of (kind, text-or-name) tuples so non-deterministic UUIDs
# don't break parity.
STREAM_CORPUS: list[tuple[str, list[str]]] = [
    (
        "single_chunk_text",
        ["Hello, world!"],
    ),
    (
        "split_text",
        ["Hel", "lo, ", "world!"],
    ),
    (
        "thinking_block",
        ["<think>let me think</think>final answer"],
    ),
    (
        "thinking_split_across_chunks",
        ["<thi", "nk>let me think</thi", "nk>final answer"],
    ),
    (
        "json_tool_call_single_chunk",
        ['<tool_call>{"tool": "bash", "args": {"command": "ls"}}</tool_call>'],
    ),
    (
        "json_tool_call_split",
        [
            '<tool_call>{"tool": "ba',
            'sh", "args": {"command": "ls"}}</to',
            "ol_call>",
        ],
    ),
    (
        "glm_variant_6_default_hints",
        ['<tool_call>web_search}{"query":"a"}</arg_value>'],
    ),
    (
        "glm_variant_6_arrow_chain_default_hints",
        [
            '<tool_call>web_search}{"query":"a"}</arg_value>',
            "\u2192",
            '<tool_call>web_search}{"query":"b"}</arg_value>',
        ],
    ),
    (
        "harmony_variant_7_default_hints",
        [
            "<tool_call>web_search\n"
            "<arg_key>query</arg_key><arg_value>news</arg_value>\n"
            "<arg_key>max_results</arg_key><arg_value>5</arg_value>\n"
            "</tool_call>",
        ],
    ),
    (
        "implicit_thinking_default_off",
        ["</think>actual answer"],
    ),
]


def _serialize_stream_events(events) -> list[dict]:
    out: list[dict] = []
    for e in events:
        item: dict = {"kind": e.kind.value}
        if e.text:
            item["text"] = e.text
        if e.tool_call is not None:
            item["tool_call"] = {
                "name": e.tool_call.name,
                "args": e.tool_call.args,
                "source": e.tool_call.source,
            }
        out.append(item)
    return out


def _capture_stream() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for label, chunks in STREAM_CORPUS:
        parser = StreamParser()  # default kwargs = v2.2.5 GLM-friendly
        events = []
        for chunk in chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.flush())
        out[label] = _serialize_stream_events(events)
    return out


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    _FIXTURES.mkdir(parents=True, exist_ok=True)

    prompts = _capture_prompts()
    (_FIXTURES / "pre_v13_prompt_snapshot.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"captured {len(prompts)} prompt entries")

    parser = _capture_parser()
    (_FIXTURES / "pre_v13_parser_snapshot.json").write_text(
        json.dumps(parser, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"captured {len(parser)} parser entries")

    stream = _capture_stream()
    (_FIXTURES / "pre_v13_stream_snapshot.json").write_text(
        json.dumps(stream, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"captured {len(stream)} stream entries")


if __name__ == "__main__":
    main()

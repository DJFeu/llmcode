# Hermes capture fixtures

Each `.txt` file in this directory is the **verbatim** raw stream output
from a real model response that triggered a parser bug. The fixture
replay test (`tests/test_tools/test_parsing_fixture_replay.py`)
discovers them automatically and asserts that
`parse_tool_calls(text, None)` returns at least one `ParsedToolCall`
with a non-empty `name`. Args may be empty for the
"truncated_invalid_json" edge case.

## How to add a new capture

When a model emits a tool_call format the parser doesn't handle:

1. Reproduce the failure with the temporary diagnostic patch in
   `conversation.py` that writes to `/tmp/llm_code_parse_debug.log`
2. Copy the `--- response_text BEGIN --- ... END ---` block
3. Save it to `YYYY-MM-DD-<short-name>.txt` in this directory
4. Run `pytest tests/test_tools/test_parsing_fixture_replay.py -v`
   — the new fixture will be auto-discovered. The test will FAIL
   until the parser is fixed.
5. Fix the parser, re-run the fixture replay, then commit both the
   parser fix AND the fixture in the same PR.

This is the "regression museum" — every parser bug we've ever seen
in production lives here forever and is replayed on every test run.

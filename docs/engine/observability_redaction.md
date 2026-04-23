# Observability Redaction

> **Status:** v12 M6 — a regex-based redactor scrubs log records and
> span attributes before anything leaves the process. Two-layer
> design: the log filter (`RedactingFilter`) handles the standard
> `logging` pipeline; the span allow-list (`ALLOWED_ATTRIBUTE_KEYS`)
> prevents hand-rolled keys from reaching the exporter.

## Section table of contents

1. What gets redacted
2. Placeholder shape
3. The 11 default patterns
4. Log redaction layer
5. Span-attribute layer
6. Extending for user-specific secrets
7. Testing redaction

## 1. What gets redacted

`llm_code.engine.observability.redaction.Redactor` runs a tuple of
compiled regexes over text input and rewrites every match to a
length-preserving placeholder. The same `Redactor` instance is
used by both the logging filter and the span-attribute scrubber,
so adding a pattern in one place covers both surfaces.

## 2. Placeholder shape

```text
# short matches (< 16 chars)
[REDACTED]

# long matches
[REDACTED:abc…xyz:len=64]
```

Longer secrets keep the first 3 + last 3 chars and the length so
operators debugging a trace can recognise *which* credential
leaked without being able to reconstruct it. The format is
deterministic — used across log messages, span attributes, and
Langfuse payloads.

## 3. The 11 default patterns

Defined in `redaction.py::DEFAULT_PATTERNS`, evaluated in order
(more-specific first so a generic pattern can't eat a specific
match):

| # | Target | Regex fragment |
|---|--------|----------------|
| 1 | Anthropic keys | `sk-ant-[A-Za-z0-9_\-]{10,}` |
| 2 | OpenAI-style `sk-` keys | `sk-[A-Za-z0-9_\-]{20,}` |
| 3 | GitHub classic PAT | `ghp_[A-Za-z0-9_]{20,}` |
| 4 | GitHub fine-grained PAT | `github_pat_[A-Za-z0-9_]{20,}` |
| 5 | JWT (3 base64url parts) | `eyJ[...]\.eyJ[...]\.[...]` |
| 6 | Bearer headers | `(?i)Bearer\s+[A-Za-z0-9._\-]{15,}` |
| 7 | AWS access key IDs | `AKIA[A-Z0-9]{10,}` |
| 8 | GCP API keys | `AIza[A-Za-z0-9_\-]{20,}` |
| 9 | Slack tokens | `xox[abpr]-[A-Za-z0-9\-]{10,}` |
| 10 | Email addresses | `…@…\.[A-Za-z]{2,}` |
| 11 | Long base64 blobs | `[A-Za-z0-9+/=_\-]{120,}` |

Pattern 11 is the safety net — it catches dumped private keys,
pasted cookies, and other credentials that don't match one of the
named patterns above. Keeping it last means a specific pattern
wins whenever it can.

## 4. Log redaction layer

`RedactingFilter` is a `logging.Filter`. `trace_init(config)`
attaches it to the **root logger** when
`config.redact_log_records = True` — every downstream handler
(stream, file, syslog, cloud-logging adapter) sees the scrubbed
output automatically.

```python
import logging
from llm_code.engine.observability.redaction import RedactingFilter

logging.getLogger().addFilter(RedactingFilter())
logging.info("Calling with Authorization: Bearer sk-ant-123456789012345")
# INFO:root:Calling with Authorization: [REDACTED:Bea…345:len=38]
```

The filter scrubs both `record.msg` and every string in
`record.args` — tuple and dict arg shapes are both handled. Non-
string args (ints, dataclasses, dict values) pass through
untouched because only strings can carry raw credentials from the
call sites we know about.

## 5. Span-attribute layer

Attribute redaction is enforced **at the source** via an
allow-list:

```python
from llm_code.engine.observability.attributes import (
    ALLOWED_ATTRIBUTE_KEYS, set_attr_safe, args_hash,
)
```

`set_attr_safe(span, key, value)` raises `ValueError` when `key`
isn't in `ALLOWED_ATTRIBUTE_KEYS`. The design rationale:

- **Blocklist approaches don't scale** — a new call site can
  invent a new key name and bypass the scrubber.
- **Allow-list is explicit** — every new attribute must be added
  to the frozen set AND documented in
  `observability_attribute_reference.md`. The attribute test
  in CI asserts the two stay in sync.

The frozen set today covers pipeline / component / agent / tool /
model (GenAI conventions) / session attributes. Raw tool
arguments are **never** allowed — use
`args_hash(args)` to store a 16-char SHA-256 digest on the
`llmcode.tool.args_hash` attribute instead.

## 6. Extending for user-specific secrets

Two options, ordered from least invasive.

### Option A — layer on top of defaults

```python
import re
from llm_code.engine.observability.redaction import (
    DEFAULT_PATTERNS, Redactor, RedactingFilter,
)

COMPANY_INTERNAL = re.compile(r"acme-tok-[A-Za-z0-9]{24,}")
CUSTOMER_ID     = re.compile(r"cust_[0-9a-f]{16}")

patterns = [COMPANY_INTERNAL, CUSTOMER_ID, *DEFAULT_PATTERNS]
redactor = Redactor(patterns=patterns)
import logging; logging.getLogger().addFilter(RedactingFilter(redactor))
```

Put company-specific patterns **before** the defaults so they
take precedence over the generic base64 matcher.

### Option B — add the pattern upstream

When the secret shape is industry-standard (new cloud vendor,
new SDK), PR the pattern into
`llm_code/engine/observability/redaction.py::DEFAULT_PATTERNS`.
Required in the PR:

- A regex that never false-positives against plain English.
- An anchor test in
  `tests/test_engine/observability/test_redaction.py` asserting
  both match and placeholder shape.
- A row in this file's §3 table.

## 7. Testing redaction

Every new pattern needs three test cases:

1. **Positive** — a canonical secret is rewritten and the
   placeholder shape matches the `[REDACTED:xxx…yyy:len=N]`
   contract.
2. **Negative** — nearby English ("my api key is…") does not
   trigger the pattern.
3. **Interaction** — if your pattern could overlap with the
   generic base64 matcher, assert your specific placeholder wins.

Two-layer coverage:

- `tests/test_engine/observability/test_redaction.py` covers the
  log-record path.
- `tests/test_engine/observability/test_attributes.py` covers the
  span-attribute allow-list; the table parity test asserts
  `ALLOWED_ATTRIBUTE_KEYS` matches the attribute-reference doc.

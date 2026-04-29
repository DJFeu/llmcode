"""Parser variant registry — v13 Phase A.

Turns the 6 hardcoded tool-call parser variants into named plugins
held by a ``REGISTRY`` dict. Profile TOML declares which variants are
enabled and in what order via ``profile.parser_variants``. When a
profile omits that field, the registry falls back to
``DEFAULT_VARIANT_ORDER`` which mirrors the historical sequence in
``parsing._parse_xml``.

Each variant is a ``ParserVariant`` instance: ``match`` is a cheap
predicate that peeks at the raw body, ``parse`` returns a
``ParsedToolCall`` or ``None``. ``requires_standard_close_when`` is a
hint consumed by ``view/stream_parser.StreamParser`` — if any listed
substring appears in an in-progress ``<tool_call>`` buffer, the
stream parser waits for the real ``</tool_call>`` close tag instead
of honouring custom close tags (avoids eating interior tags that
belong to the variant body, e.g. ``</arg_value>`` in Harmony).

Plugin variants live outside this module. A profile that lists
``"my_pkg.my_mod:MyVariant"`` triggers ``load_plugin_variant`` which
``importlib.import_module``s the module, ``getattr``s the attribute
and validates it's a ``ParserVariant`` instance before registering.

Built-in variants (ordered per DEFAULT_VARIANT_ORDER):

1. ``json_payload``      — ``{"tool": "NAME", "args": {...}}``
2. ``hermes_function``   — ``<function=NAME>...</function>`` (full +
                           template-truncated)
3. ``hermes_truncated``  — bare identifier at the top of the body,
                           ``NAME>...`` or ``NAME{...}``
4. ``harmony_kv``        — ``<arg_key>/<arg_value>`` pair body
                           (variant 7)
5. ``glm_brace``         — ``NAME}{JSON}`` body at the top
                           (variant 6)
6. ``bare_name_tag``     — ``<NAME>JSON</NAME>`` wrapper-less
                           (variant 5)
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import re
import uuid
from typing import Callable

from llm_code.tools.parsing import (
    _GLM_TOOL_CALL_VARIANT_RE,
    _HERMES_BARE_NAME_TAG_RE,
    _HERMES_FUNCTION_TRUNCATED_RE,
    _VARIANT_5_RESERVED_NAMES,
    _WEBFETCH_INLINE_RE,
    ParsedToolCall,
    _parse_bare_name_tag,
    _parse_glm_hybrid_variant,
    _parse_glm_variant,
    _parse_harmony_variant,
    _parse_hermes_block,
    _parse_json_payload,
    _parse_webfetch_inline,
)


# ── Errors ────────────────────────────────────────────────────────────


class UnknownVariantError(KeyError):
    """Raised when a profile declares a variant name that's neither
    registered nor a valid plugin dotted path."""


class PluginLoadError(RuntimeError):
    """Raised when ``load_plugin_variant`` can't resolve the dotted
    path to a valid ``ParserVariant`` instance."""


# ── Dataclass ─────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ParserVariant:
    """Named tool-call parser plugin.

    ``match`` is a cheap predicate — peek at the raw body to decide
    whether this variant has any chance of parsing. ``parse`` returns
    a ``ParsedToolCall`` or ``None``; ``None`` means "I look similar
    but the body didn't parse cleanly, try the next variant".

    ``requires_standard_close_when`` — stream-parser hint: if any of
    these substrings appears in an in-progress ``<tool_call>``
    buffer, the stream parser MUST wait for ``</tool_call>`` and
    ignore custom close tags (avoids eating interior tags that
    belong to this variant's body).
    """
    name: str
    match: Callable[[str], bool]
    parse: Callable[[str], ParsedToolCall | None]
    requires_standard_close_when: tuple[str, ...] = ()


# ── Registry ──────────────────────────────────────────────────────────


REGISTRY: dict[str, ParserVariant] = {}


def register_variant(variant: ParserVariant) -> None:
    """Register a variant by name. Overwrites silently — callers
    that want duplicate-detection must check ``variant.name in
    REGISTRY`` first."""
    REGISTRY[variant.name] = variant


def list_variant_names() -> tuple[str, ...]:
    """Return all registered variant names as a sorted tuple. Useful
    for diagnostics and for authoring profile TOML files."""
    return tuple(sorted(REGISTRY.keys()))


def load_plugin_variant(dotted: str) -> ParserVariant:
    """Import ``dotted`` like ``my_pkg.my_mod:MyVariant`` and return
    the attribute as a ``ParserVariant`` instance.

    Raises ``PluginLoadError`` on:

    - Missing ``:`` separator in the dotted path
    - ``importlib.import_module`` failure (module not on ``sys.path``)
    - Attribute not present on the module
    - Attribute present but not a ``ParserVariant`` instance

    Note: the loader only accepts dotted paths that resolve via
    ``sys.path``. No dynamic eval, no URL fetch, no exec of string
    bodies. Plugins are trusted code — audit before enabling.
    """
    if ":" not in dotted:
        raise PluginLoadError(
            f"expected module:attr, got {dotted!r}"
        )
    mod_path, attr = dotted.split(":", 1)
    try:
        module = importlib.import_module(mod_path)
    except ImportError as exc:
        raise PluginLoadError(
            f"cannot import module {mod_path!r}: {exc}"
        ) from exc
    if not hasattr(module, attr):
        raise PluginLoadError(
            f"module {mod_path!r} has no attribute {attr!r}"
        )
    variant = getattr(module, attr)
    if not isinstance(variant, ParserVariant):
        raise PluginLoadError(
            f"{dotted!r} is not a ParserVariant instance "
            f"(got {type(variant).__name__})"
        )
    return variant


def get_variant(name: str) -> ParserVariant:
    """Resolve a variant name.

    Resolution order:

    1. Exact match in ``REGISTRY``
    2. Contains ``:`` → ``load_plugin_variant`` + auto-register
    3. Otherwise raise ``UnknownVariantError``
    """
    if name in REGISTRY:
        return REGISTRY[name]
    if ":" in name:
        variant = load_plugin_variant(name)
        register_variant(variant)
        return variant
    raise UnknownVariantError(name)


# ── Default order ─────────────────────────────────────────────────────

# Mirrors the pre-v13 sequence in ``parsing._parse_xml`` exactly,
# extended with v15 M5's inline WebFetch detector at the end (lowest
# priority — only fires when no earlier wrapper-based variant
# matched). Parity is the gate: a test that declares profile=None
# must produce the same list of ParsedToolCalls as the profile-
# driven sequence.
DEFAULT_VARIANT_ORDER: tuple[str, ...] = (
    "json_payload",
    "hermes_function",
    "hermes_truncated",
    "harmony_kv",
    # v2.13.2 — placed BETWEEN harmony_kv and glm_brace. Proper
    # harmony emissions match harmony_kv first; only the malformed
    # GLM hybrid shape (no ``</arg_key>`` close) falls through here.
    # Placed BEFORE glm_brace because the hybrid match is more
    # specific (requires literal ``<arg_key>args``) and proper
    # ``NAME}{JSON}`` shapes don't satisfy this pattern.
    "glm_hybrid",
    "glm_brace",
    "bare_name_tag",
    "webfetch_inline",  # v15 M5 — last priority; wrapper-less, gated
)


# ── Match predicates ──────────────────────────────────────────────────


def _match_json_payload(raw: str) -> bool:
    """``{"tool": "NAME", "args": {...}}`` at the top of the body."""
    s = raw.lstrip()
    return s.startswith("{") and '"tool"' in s


def _match_hermes_function(raw: str) -> bool:
    """``<function=NAME>...</function>`` full form appears anywhere."""
    return "<function=" in raw


def _match_hermes_truncated(raw: str) -> bool:
    """Template-truncated Hermes — bare identifier followed by ``>``
    or ``{`` at the top of the body. The full-form match above takes
    precedence; this is only tried if ``<function=`` isn't present."""
    return _HERMES_FUNCTION_TRUNCATED_RE.match(raw) is not None


def _match_harmony_kv(raw: str) -> bool:
    """Harmony/GLM key-value pair body — contains ``<arg_key>``."""
    return "<arg_key>" in raw


# Compiled once at module load for cheap repeated matching.
_MATCH_GLM_BRACE_RE = re.compile(
    r"^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\s*\{",
    re.DOTALL,
)


def _match_glm_brace(raw: str) -> bool:
    """GLM-5.1 variant 6 — ``NAME}{JSON}`` at the top of the body.
    The closing ``</arg_value>`` sits OUTSIDE the raw body in the
    wrapper-less case, so the match only looks at the body opening."""
    return _MATCH_GLM_BRACE_RE.match(raw) is not None


# v2.13.2 — cheap precondition check for the GLM hybrid shape.
# ``<arg_key>args`` is the structural marker; the full extraction
# regex (``_GLM_HYBRID_TOOL_CALL_RE``) does the heavy work in the
# parse function. This match predicate is permissive on purpose —
# proper harmony_kv content with an explicit ``args`` argument
# (``<arg_key>args</arg_key><arg_value>...</arg_value>``) WOULD also
# return True here, but harmony_kv runs FIRST in the variant order
# and parses those cleanly, so glm_hybrid only ever fires on the
# malformed shape that harmony rejected.
_MATCH_GLM_HYBRID_RE = re.compile(r"<arg_key>args[\"']?")


def _match_glm_hybrid(raw: str) -> bool:
    """v2.13.2 — GLM hybrid emission with ``<arg_key>args`` pseudo-key
    wrapping the JSON args dict. Variant order ensures harmony_kv
    handles proper emissions first; this predicate only needs a
    cheap structural check."""
    return _MATCH_GLM_HYBRID_RE.search(raw) is not None


def _match_bare_name_tag(raw: str) -> bool:
    """Wrapper-less ``<NAME>JSON</NAME>`` — the raw body here is the
    full text (not a wrapped body) because this variant never sees a
    ``<tool_call>`` wrapper. Match is against the whole string."""
    return _HERMES_BARE_NAME_TAG_RE.search(raw) is not None


def _match_webfetch_inline(raw: str) -> bool:
    """v15 M5 — ``WebFetch{…}`` / ``WebSearch{…}`` / lowercase variants
    in plain text without any XML wrapper. Matches the full text;
    gating on ``known_tool_names`` happens inside the parse function
    so the wrapper-less scanner can verify the registered tool list."""
    return _WEBFETCH_INLINE_RE.search(raw) is not None


# ── Wrapper adapters for variants that scan full text ─────────────────
#
# Some variants (glm_brace, bare_name_tag) were originally written to
# scan the whole text and return a LIST of calls. The registry
# contract is "parse a single raw body → single ParsedToolCall |
# None", so we provide single-call adapters here. The multi-call
# scanners still run as wrapper-less fallbacks in ``_parse_xml``.


def _parse_glm_variant_single(raw: str) -> ParsedToolCall | None:
    """Single-call adapter: run ``_GLM_TOOL_CALL_VARIANT_RE`` on the
    raw body and return the first valid match.

    The regex still requires the ``<tool_call>`` wrapper — if the
    stream parser stripped it already, we add it back for the regex
    to match. This lets the variant slot into the normal per-block
    loop without needing wrapper-less treatment at that layer.
    """
    candidate = raw.lstrip()
    if not candidate.startswith("<tool_call>"):
        # Reconstruct a minimal wrapper so the regex, which anchors
        # on ``<tool_call>``, can still match the body content.
        candidate = f"<tool_call>{candidate}</arg_value>"
    m = _GLM_TOOL_CALL_VARIANT_RE.search(candidate)
    if m is None:
        return None
    name = m.group(1).strip()
    if not name or name in _VARIANT_5_RESERVED_NAMES:
        return None
    try:
        args = json.loads(m.group(2))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(args, dict):
        return None
    return ParsedToolCall(
        id=str(uuid.uuid4()),
        name=name,
        args=args,
        source="xml_tag",
    )


def _parse_glm_hybrid_variant_single(raw: str) -> ParsedToolCall | None:
    """v2.13.2 — single-call adapter for the GLM hybrid shape.

    Mirrors :func:`_parse_glm_variant_single` — reconstructs the
    ``<tool_call>...</arg_value>`` envelope when the stream parser
    has stripped it, runs the multi-call scanner, returns the first
    match. The per-block loop only ever lands here when harmony_kv
    rejected the body (no ``</arg_key>`` pairs), so false-positives
    against proper harmony emissions are structurally impossible.
    """
    candidate = raw.lstrip()
    if not candidate.startswith("<tool_call>"):
        candidate = f"<tool_call>{candidate}</arg_value>"
    matches = _parse_glm_hybrid_variant(candidate)
    return matches[0] if matches else None


def _parse_bare_name_tag_single(raw: str) -> ParsedToolCall | None:
    """Single-call adapter: find the first valid ``<NAME>JSON</NAME>``
    match in the raw body and return one ``ParsedToolCall``.

    Does not enforce ``known_tool_names`` — that guard still fires
    at the multi-call fallback level (``_parse_bare_name_tag`` in
    ``parsing.py``) when wrapper-less scanning runs."""
    matches = _parse_bare_name_tag(raw, known_tool_names=None)
    return matches[0] if matches else None


def _parse_webfetch_inline_single(raw: str) -> ParsedToolCall | None:
    """Single-call adapter for v15 M5.

    The wrapper-less scanner branch runs the full multi-call
    ``_parse_webfetch_inline`` (which gates on ``known_tool_names``).
    The per-block path here is permissive (``known_tool_names=None``)
    because the per-block loop is only reached after a ``<tool_call>``
    wrapper match — and inside such a wrapper, the inline shape
    almost never appears. Returning the first match keeps the
    contract consistent with the other single-call adapters.
    """
    matches = _parse_webfetch_inline(raw, known_tool_names=None)
    return matches[0] if matches else None


# ── Built-in variant instances ────────────────────────────────────────


json_payload_variant = ParserVariant(
    name="json_payload",
    match=_match_json_payload,
    parse=_parse_json_payload,
)

hermes_function_variant = ParserVariant(
    name="hermes_function",
    match=_match_hermes_function,
    parse=_parse_hermes_block,
)

hermes_truncated_variant = ParserVariant(
    name="hermes_truncated",
    match=_match_hermes_truncated,
    # Same parse function — ``_parse_hermes_block`` already tries
    # both full and truncated forms internally, and the match
    # predicate above guards against false positives.
    parse=_parse_hermes_block,
)

harmony_kv_variant = ParserVariant(
    name="harmony_kv",
    match=_match_harmony_kv,
    parse=_parse_harmony_variant,
    requires_standard_close_when=("<arg_key>",),
)

glm_hybrid_variant = ParserVariant(
    name="glm_hybrid",
    match=_match_glm_hybrid,
    parse=_parse_glm_hybrid_variant_single,
)

glm_brace_variant = ParserVariant(
    name="glm_brace",
    match=_match_glm_brace,
    parse=_parse_glm_variant_single,
)

bare_name_tag_variant = ParserVariant(
    name="bare_name_tag",
    match=_match_bare_name_tag,
    parse=_parse_bare_name_tag_single,
)

webfetch_inline_variant = ParserVariant(
    name="webfetch_inline",
    match=_match_webfetch_inline,
    parse=_parse_webfetch_inline_single,
)


# Register all built-ins at module import.
register_variant(json_payload_variant)
register_variant(hermes_function_variant)
register_variant(hermes_truncated_variant)
register_variant(harmony_kv_variant)
register_variant(glm_hybrid_variant)
register_variant(glm_brace_variant)
register_variant(bare_name_tag_variant)
register_variant(webfetch_inline_variant)


# Re-export multi-call fallback scanners so ``parsing._parse_xml`` can
# reach them without a second import chain.
_WRAPPER_LESS_SCANNERS: dict[str, Callable[[str, object], list[ParsedToolCall]] | Callable[[str], list[ParsedToolCall]]] = {
    # v2.13.2 — glm_hybrid registered BEFORE glm_brace so chained
    # parallel emissions whose only close tag is ``</arg_value>``
    # (no ``</tool_call>`` to drive the per-block loop) reach the
    # hybrid scanner before glm_brace, which would otherwise bail
    # because the body has ``<arg_key>`` instead of the literal
    # ``}{`` separator it expects.
    "glm_hybrid": _parse_glm_hybrid_variant,
    "glm_brace": _parse_glm_variant,
    "bare_name_tag": _parse_bare_name_tag,
    # v15 M5 — wrapper-less inline WebFetch / WebSearch JSON detection.
    # Takes ``known_tool_names`` so the registry guard fires on the
    # multi-call scanner path (the per-block adapter above runs in
    # permissive mode).
    "webfetch_inline": _parse_webfetch_inline,
}

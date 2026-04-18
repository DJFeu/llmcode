"""Minimal i18n (M11).

Resolves ``LLMCODE_LANG`` at first lookup; callers use ``t(key, **kw)``
with dot-notation keys. Missing keys fall through to English and
ultimately return the key itself, so new strings never raise.
"""
from __future__ import annotations

import os


_CATALOGS: dict[str, dict[str, str]] = {
    "en": {
        "error.file_not_found": "File not found",
        "error.file_too_large": "File too large",
        "error.file_size": "File too large: {path} ({size} bytes)",
        "error.generic": "Something went wrong",
        "status.ready": "Ready",
    },
    "zh-TW": {
        "error.file_not_found": "找不到檔案",
        "error.file_too_large": "檔案過大",
        "error.file_size": "檔案過大：{path}（{size} bytes）",
        "status.ready": "就緒",
    },
}

_language: str | None = None


def _resolve_language() -> str:
    env = os.environ.get("LLMCODE_LANG", "").strip()
    if env in _CATALOGS:
        return env
    return "en"


def current_language() -> str:
    global _language
    if _language is None:
        _language = _resolve_language()
    return _language


def _reset_language_for_tests() -> None:
    global _language
    _language = None


def t(key: str, **params) -> str:
    """Translate ``key`` to the current language.

    Missing keys in the active language fall back to English; missing
    in both returns the key unchanged. ``params`` are applied as
    ``str.format`` templates.
    """
    lang = current_language()
    catalog = _CATALOGS.get(lang, _CATALOGS["en"])
    text = catalog.get(key)
    if text is None:
        text = _CATALOGS["en"].get(key, key)
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text
    return text

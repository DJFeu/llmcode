"""M11: i18n translation basics."""
from __future__ import annotations


class TestTranslation:
    def test_default_language_is_english(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.delenv("LLMCODE_LANG", raising=False)
        i18n._reset_language_for_tests()
        assert i18n.current_language() == "en"

    def test_env_var_overrides(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.setenv("LLMCODE_LANG", "zh-TW")
        i18n._reset_language_for_tests()
        assert i18n.current_language() == "zh-TW"

    def test_translate_known_key_chinese(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.setenv("LLMCODE_LANG", "zh-TW")
        i18n._reset_language_for_tests()
        assert i18n.t("error.file_not_found") == "找不到檔案"

    def test_translate_unknown_key_returns_key(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.setenv("LLMCODE_LANG", "zh-TW")
        i18n._reset_language_for_tests()
        assert i18n.t("nonexistent.key") == "nonexistent.key"

    def test_fallback_to_english(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.setenv("LLMCODE_LANG", "zh-TW")
        i18n._reset_language_for_tests()
        assert "Something went wrong" in i18n.t("error.generic")

    def test_interpolation(self, monkeypatch) -> None:
        from llm_code import i18n
        monkeypatch.setenv("LLMCODE_LANG", "en")
        i18n._reset_language_for_tests()
        assert i18n.t("error.file_size", path="/tmp/x", size=123) == \
            "File too large: /tmp/x (123 bytes)"

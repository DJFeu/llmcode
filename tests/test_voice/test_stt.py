"""Tests for STT protocol, factory, and language map."""
from __future__ import annotations

from llm_code.voice.languages import LANGUAGE_MAP, validate_language
from llm_code.voice.stt import STTEngine, create_stt_engine
from llm_code.runtime.config import VoiceConfig


class TestLanguageMap:
    def test_has_minimum_entries(self):
        assert len(LANGUAGE_MAP) >= 26

    def test_known_languages(self):
        for code in ("en", "zh", "ja", "ko", "es", "fr", "de", "pt", "ru", "ar"):
            assert code in LANGUAGE_MAP

    def test_validate_known(self):
        assert validate_language("zh") == "zh"

    def test_validate_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Unsupported language"):
            validate_language("xx_fake")


class TestSTTProtocol:
    def test_protocol_shape(self):
        """STTEngine must define transcribe(audio_bytes, language) -> str."""
        import inspect
        sig = inspect.signature(STTEngine.transcribe)
        params = list(sig.parameters.keys())
        assert "audio_bytes" in params
        assert "language" in params


class TestCreateSTTEngine:
    def test_whisper_backend(self):
        cfg = VoiceConfig(backend="whisper")
        engine = create_stt_engine(cfg)
        assert engine is not None
        assert hasattr(engine, "transcribe")

    def test_unknown_backend_raises(self):
        import pytest
        cfg = VoiceConfig(backend="nonexistent")
        with pytest.raises(ValueError, match="Unknown STT backend"):
            create_stt_engine(cfg)

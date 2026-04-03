"""Tests for voice configuration."""
from __future__ import annotations

from llm_code.runtime.config import RuntimeConfig, VoiceConfig, load_config


class TestVoiceConfig:
    def test_defaults(self):
        vc = VoiceConfig()
        assert vc.enabled is False
        assert vc.backend == "whisper"
        assert vc.whisper_url == "http://localhost:8000/v1/audio/transcriptions"
        assert vc.language == "en"
        assert vc.hotkey == "ctrl+space"

    def test_frozen(self):
        vc = VoiceConfig()
        import dataclasses
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            vc.enabled = True  # type: ignore[misc]

    def test_runtime_config_has_voice(self):
        rc = RuntimeConfig()
        assert isinstance(rc.voice, VoiceConfig)
        assert rc.voice.enabled is False


class TestVoiceConfigLoading:
    def test_loads_voice_from_json(self, tmp_path):
        import json
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "voice": {
                "enabled": True,
                "backend": "google",
                "language": "zh",
                "hotkey": "f5",
            }
        }))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.voice.enabled is True
        assert rc.voice.backend == "google"
        assert rc.voice.language == "zh"
        assert rc.voice.hotkey == "f5"

    def test_missing_voice_uses_defaults(self, tmp_path):
        import json
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"model": "test"}))
        rc = load_config(
            user_dir=tmp_path / "no_user",
            project_dir=tmp_path / "no_proj",
            local_path=cfg_file,
            cli_overrides={},
        )
        assert rc.voice.enabled is False
        assert rc.voice.backend == "whisper"

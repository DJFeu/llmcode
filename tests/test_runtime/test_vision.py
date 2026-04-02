"""Tests for llm_code.runtime.vision (Task 21)."""
from __future__ import annotations

import dataclasses
import pytest

from llm_code.api.types import ImageBlock, Message, TextBlock
from llm_code.runtime.config import VisionConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(*blocks) -> Message:
    return Message(role="user", content=tuple(blocks))


def _text(t: str) -> TextBlock:
    return TextBlock(text=t)


def _image(media_type: str = "image/png", data: str = "abc123") -> ImageBlock:
    return ImageBlock(media_type=media_type, data=data)


# ---------------------------------------------------------------------------
# VisionFallback.is_configured
# ---------------------------------------------------------------------------

class TestVisionFallbackIsConfigured:
    def test_true_when_both_fields_set(self):
        from llm_code.runtime.vision import VisionFallback

        cfg = VisionConfig(vision_model="llava", vision_api="http://localhost:11434")
        vf = VisionFallback(cfg)
        assert vf.is_configured() is True

    def test_false_when_vision_model_empty(self):
        from llm_code.runtime.vision import VisionFallback

        cfg = VisionConfig(vision_model="", vision_api="http://localhost:11434")
        vf = VisionFallback(cfg)
        assert vf.is_configured() is False

    def test_false_when_vision_api_empty(self):
        from llm_code.runtime.vision import VisionFallback

        cfg = VisionConfig(vision_model="llava", vision_api="")
        vf = VisionFallback(cfg)
        assert vf.is_configured() is False

    def test_false_when_both_empty(self):
        from llm_code.runtime.vision import VisionFallback

        cfg = VisionConfig()
        vf = VisionFallback(cfg)
        assert vf.is_configured() is False


# ---------------------------------------------------------------------------
# preprocess_images — passthrough cases
# ---------------------------------------------------------------------------

class TestPreprocessPassthrough:
    def test_passthrough_when_supports_images(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_text("hello"), _image())
        result = preprocess_images(msg, supports_images=True, vision_fallback=None)
        assert result is msg

    def test_passthrough_when_no_images_no_support(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_text("text only"))
        result = preprocess_images(msg, supports_images=False, vision_fallback=None)
        assert result is msg

    def test_passthrough_empty_message(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg()
        result = preprocess_images(msg, supports_images=False, vision_fallback=None)
        assert result is msg


# ---------------------------------------------------------------------------
# preprocess_images — strip when no fallback
# ---------------------------------------------------------------------------

class TestPreprocessStrip:
    def test_strips_images_no_fallback(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_text("describe this"), _image())
        result = preprocess_images(msg, supports_images=False, vision_fallback=None)
        assert isinstance(result, Message)
        assert all(not isinstance(b, ImageBlock) for b in result.content)

    def test_strips_images_returns_warning(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_image(), _text("hi"))
        result, warnings = preprocess_images(
            msg, supports_images=False, vision_fallback=None, return_warnings=True
        )
        assert len(warnings) >= 1
        assert any("image" in w.lower() for w in warnings)

    def test_preserves_text_blocks_when_stripping(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_text("keep me"), _image(), _text("and me"))
        result = preprocess_images(msg, supports_images=False, vision_fallback=None)
        texts = [b.text for b in result.content if isinstance(b, TextBlock)]
        assert "keep me" in texts
        assert "and me" in texts

    def test_return_warnings_false_returns_message_only(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_image())
        result = preprocess_images(
            msg, supports_images=False, vision_fallback=None, return_warnings=False
        )
        assert isinstance(result, Message)

    def test_no_warning_when_no_images_stripped(self):
        from llm_code.runtime.vision import preprocess_images

        msg = _make_msg(_text("no images here"))
        result, warnings = preprocess_images(
            msg, supports_images=False, vision_fallback=None, return_warnings=True
        )
        assert warnings == []


# ---------------------------------------------------------------------------
# preprocess_images — with unconfigured fallback (acts like no fallback)
# ---------------------------------------------------------------------------

class TestPreprocessUnconfiguredFallback:
    def test_strips_when_fallback_not_configured(self):
        from llm_code.runtime.vision import VisionFallback, preprocess_images

        cfg = VisionConfig()  # no vision_model, no vision_api
        vf = VisionFallback(cfg)
        msg = _make_msg(_image())
        result = preprocess_images(msg, supports_images=False, vision_fallback=vf)
        assert isinstance(result, Message)
        assert all(not isinstance(b, ImageBlock) for b in result.content)

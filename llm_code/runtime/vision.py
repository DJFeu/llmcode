"""Vision fallback: replace ImageBlocks when the primary model has no vision support."""
from __future__ import annotations

import dataclasses
import os

from llm_code.api.types import ImageBlock, Message, TextBlock
from llm_code.runtime.config import VisionConfig


class VisionFallback:
    """Wraps VisionConfig and can optionally call a vision API to describe images."""

    def __init__(self, config: VisionConfig) -> None:
        self._config = config

    def is_configured(self) -> bool:
        """Return True if both vision_model and vision_api are non-empty."""
        return bool(self._config.vision_model) and bool(self._config.vision_api)

    async def describe_image(self, image: ImageBlock) -> str:
        """Call the vision API and return a text description of the image."""
        from llm_code.api.openai_compat import OpenAICompatProvider
        from llm_code.api.types import Message, MessageRequest, TextBlock

        api_key = ""
        if self._config.vision_api_key_env:
            api_key = os.environ.get(self._config.vision_api_key_env, "")

        provider = OpenAICompatProvider(
            base_url=self._config.vision_api,
            api_key=api_key,
            model_name=self._config.vision_model,
        )
        try:
            request = MessageRequest(
                model=self._config.vision_model,
                messages=(
                    Message(
                        role="user",
                        content=(
                            image,
                            TextBlock(text="Describe this image in detail."),
                        ),
                    ),
                ),
                stream=False,
            )
            response = await provider.send_message(request)
            for block in response.content:
                if isinstance(block, TextBlock):
                    return block.text
            return ""
        finally:
            await provider.close()


def preprocess_images(
    msg: Message,
    supports_images: bool,
    vision_fallback: "VisionFallback | None",
    return_warnings: bool = False,
) -> "Message | tuple[Message, list[str]]":
    """Pre-process a message's image blocks based on vision support.

    - If model supports images OR message has no images → passthrough unchanged.
    - If vision_fallback is configured → replace each ImageBlock with a placeholder
      TextBlock (sync version; describe_image is not called here).
    - Otherwise → strip ImageBlocks, emit a warning.
    """
    has_images = any(isinstance(b, ImageBlock) for b in msg.content)

    if supports_images or not has_images:
        if return_warnings:
            return msg, []
        return msg

    warnings: list[str] = []

    if vision_fallback is not None and vision_fallback.is_configured():
        # Replace each image with a placeholder; actual async describe not called here
        new_blocks = []
        for block in msg.content:
            if isinstance(block, ImageBlock):
                new_blocks.append(
                    TextBlock(text="[image: vision description not yet available]")
                )
            else:
                new_blocks.append(block)
        new_msg = dataclasses.replace(msg, content=tuple(new_blocks))
    else:
        # Strip images, keep other blocks
        new_blocks = [b for b in msg.content if not isinstance(b, ImageBlock)]
        new_msg = dataclasses.replace(msg, content=tuple(new_blocks))
        warnings.append(
            "One or more images were stripped because the model does not support "
            "vision and no vision fallback is configured."
        )

    if return_warnings:
        return new_msg, warnings
    return new_msg

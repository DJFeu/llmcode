"""Built-in :class:`AuthHandler` implementations (v16 M6).

Six providers ship in v2.6.0:

* :class:`AnthropicHandler` — API key (``ANTHROPIC_API_KEY``)
* :class:`OpenAIHandler` — API key (``OPENAI_API_KEY``)
* :class:`ZhipuHandler` — OAuth + API key (``ZHIPU_API_KEY``)
* :class:`NvidiaNimHandler` — API key + free-tier rate detection
  (``NVIDIA_API_KEY``)
* :class:`OpenRouterHandler` — API key (``OPENROUTER_API_KEY``)
* :class:`DeepSeekHandler` — API key (``DEEPSEEK_API_KEY``)

Each handler follows the same protocol: ``login()`` walks the user
through a credential acquisition flow, persists the result via
:func:`auth.save_credentials`, then ``credentials_for_request()``
returns the headers a provider HTTP client should add.
"""
from __future__ import annotations

from llm_code.runtime.auth import register_handler
from llm_code.runtime.auth.handlers.anthropic import AnthropicHandler
from llm_code.runtime.auth.handlers.deepseek import DeepSeekHandler
from llm_code.runtime.auth.handlers.nvidia_nim import NvidiaNimHandler
from llm_code.runtime.auth.handlers.openai import OpenAIHandler
from llm_code.runtime.auth.handlers.openrouter import OpenRouterHandler
from llm_code.runtime.auth.handlers.zhipu import ZhipuHandler


def register_builtins() -> None:
    """Register every shipped handler in the auth registry.

    Called lazily by :func:`auth.get_handler` on first lookup so
    plumbing-only test contexts can keep the registry empty.
    """
    register_handler(AnthropicHandler())
    register_handler(OpenAIHandler())
    register_handler(ZhipuHandler())
    register_handler(NvidiaNimHandler())
    register_handler(OpenRouterHandler())
    register_handler(DeepSeekHandler())


__all__ = [
    "AnthropicHandler",
    "DeepSeekHandler",
    "NvidiaNimHandler",
    "OpenAIHandler",
    "OpenRouterHandler",
    "ZhipuHandler",
    "register_builtins",
]

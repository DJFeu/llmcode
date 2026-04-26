"""Tool-result consumption compatibility helpers.

v14 introduces three optional mechanisms that paper over a class of
model-level instruction-following weaknesses where a model calls a
tool, receives data, and then writes a ``content`` response that
contradicts the tool result. This module hosts mechanism A only —
post-tool ``<system-reminder>`` injection. See ``denial_detector.py``
and the ``api/openai_compat.py`` history filter for the others.

The reminder is a synthetic ``user`` role :class:`Message` whose body
contains a single :class:`TextBlock` carrying a short
``<system-reminder>`` block. The runtime appends it to the outbound
history *immediately after* the bundled tool-result message, so both
ride in the same provider call as the tool result. Models then see
the correction in the most recent ~50 tokens of context — turn-
proximate, where behavioural anti-patterns surface — instead of
relying on session-frozen system-prompt text that drifts hundreds of
tokens away after a tool round-trip.

Toggle: ``profile.reminder_after_each_call`` (default True). Profiles
for models that already consume tool results reliably can opt out.
"""
from __future__ import annotations

import logging

from llm_code.api.types import Message, TextBlock
from llm_code.runtime.model_profile import ModelProfile

logger = logging.getLogger(__name__)


# Module-level reminder template. Kept short (~40 tokens) so the
# overhead per tool call stays within the budget the spec calls out
# in §3.2 of the v14 design doc. The text deliberately:
#   * names the specific tool the model just used (turn-proximate
#     grounding the system prompt cannot match);
#   * frames the result as "ground truth", overriding the model's
#     RLHF-trained habit of denying tool capabilities;
#   * tolerates empty / error results explicitly so the model has a
#     legitimate fall-through that doesn't reach for a denial.
_REMINDER_TEMPLATE = (
    "<system-reminder>\n"
    "You just called {tool_name} and received the result above. "
    "That data is your ground truth for this turn — consume it in "
    "your `content` response. Do NOT deny the tool or capability "
    "you just used. If the result is empty or an error, say so "
    "plainly and proceed.\n"
    "</system-reminder>"
)


def build_post_tool_reminder(
    tool_name: str, profile: ModelProfile,
) -> Message | None:
    """Return a synthetic ``user`` role :class:`Message` carrying a
    ``<system-reminder>`` block, or ``None`` when the profile
    disables this mechanism.

    The caller appends the returned Message to its outbound history
    *immediately after* the message carrying the tool result, so both
    ride in the same provider call as the tool result. The reminder
    is structured as a ``user`` role text block — the closest the
    OpenAI-compat protocol has to a "system reminder" mid-conversation
    that every provider accepts unmodified.

    Returns ``None`` when:
      * ``profile.reminder_after_each_call`` is False (the profile
        opted out), or
      * ``tool_name`` is empty / falsy (defensive — callers that
        cannot derive a tool name simply skip the reminder rather
        than emit a malformed reminder body).

    Logs a structured INFO event ``tool_consumption: reminder_injected``
    whenever a reminder is produced. Aggregating these per-call counts
    by tool name is the intended observability signal.
    """
    if not profile.reminder_after_each_call:
        return None
    if not tool_name:
        return None
    reminder = _REMINDER_TEMPLATE.format(tool_name=tool_name)
    logger.info(
        "tool_consumption: reminder_injected tool=%s bytes=%d",
        tool_name, len(reminder),
    )
    return Message(role="user", content=(TextBlock(text=reminder),))

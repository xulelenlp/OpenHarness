# ruff: noqa: F403, F405
"""Auto-compact state and thresholds."""

from __future__ import annotations

from openharness.engine.messages import ConversationMessage
from openharness.services.compact_parts.constants import *
from openharness.services.compact_parts.tokens import estimate_message_tokens


class AutoCompactState:
    """Mutable state that persists across query loop turns."""

    compacted: bool = False
    turn_counter: int = 0
    turn_id: str = ""
    consecutive_failures: int = 0


# ---------------------------------------------------------------------------
# Context window helpers
# ---------------------------------------------------------------------------

def get_context_window(model: str, *, context_window_tokens: int | None = None) -> int:
    """Return the context window size for a model (conservative defaults)."""
    if context_window_tokens is not None and context_window_tokens > 0:
        return int(context_window_tokens)
    m = model.lower()
    if "opus" in m:
        return 200_000
    if "sonnet" in m:
        return 200_000
    if "haiku" in m:
        return 200_000
    # Kimi / other providers — be conservative
    return _DEFAULT_CONTEXT_WINDOW


def get_autocompact_threshold(
    model: str,
    *,
    context_window_tokens: int | None = None,
    auto_compact_threshold_tokens: int | None = None,
) -> int:
    """Calculate the token count at which auto-compact fires."""
    if auto_compact_threshold_tokens is not None and auto_compact_threshold_tokens > 0:
        return int(auto_compact_threshold_tokens)
    context_window = get_context_window(model, context_window_tokens=context_window_tokens)
    reserved = min(MAX_OUTPUT_TOKENS_FOR_SUMMARY, 20_000)
    effective = context_window - reserved
    return effective - AUTOCOMPACT_BUFFER_TOKENS


def should_autocompact(
    messages: list[ConversationMessage],
    model: str,
    state: AutoCompactState,
    *,
    context_window_tokens: int | None = None,
    auto_compact_threshold_tokens: int | None = None,
) -> bool:
    """Return True when the conversation should be auto-compacted."""
    if state.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        return False
    token_count = estimate_message_tokens(messages)
    threshold = get_autocompact_threshold(
        model,
        context_window_tokens=context_window_tokens,
        auto_compact_threshold_tokens=auto_compact_threshold_tokens,
    )
    return token_count >= threshold

"""Error-classification helpers shared by the engine and compaction service."""

from __future__ import annotations

__all__ = ["is_prompt_too_long_error"]

#: Provider error snippets that mean "the prompt exceeded the context window".
PROMPT_TOO_LONG_MARKERS: tuple[str, ...] = (
    "prompt too long",
    "context_length_exceeded",
    "context length",
    "maximum context",
    "context window",
    "input tokens exceed",
    "messages resulted in",
    "reduce the length of the messages",
    "configured limit",
    "too many tokens",
    "too large for the model",
    "maximum context length",
    "exceed_context",
    "exceeds the available context size",
    "available context size",
)


def is_prompt_too_long_error(exc: Exception) -> bool:
    """Return True when *exc* looks like a provider context-window overflow."""
    text = str(exc).lower()
    return any(marker in text for marker in PROMPT_TOO_LONG_MARKERS)

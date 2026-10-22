"""Token estimation for compaction."""

from __future__ import annotations

import logging
import os

from openharness.engine.messages import (
    ContentBlock,
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.services.compact_parts.constants import (
    _DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE,
    TOKEN_ESTIMATION_PADDING,
)
from openharness.services.token_estimation import estimate_tokens

log = logging.getLogger(__name__)

def estimate_message_tokens(messages: list[ConversationMessage]) -> int:
    """Estimate total tokens for a conversation, including the 4/3 padding."""
    total = 0
    image_token_estimate = _vision_token_budget_per_image()
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total += estimate_tokens(block.text)
            elif isinstance(block, ToolResultBlock):
                total += estimate_tokens(block.content)
            elif isinstance(block, ToolUseBlock):
                total += estimate_tokens(block.name)
                total += estimate_tokens(str(block.input))
            elif isinstance(block, ImageBlock):
                total += image_token_estimate
    return int(total * TOKEN_ESTIMATION_PADDING)


def estimate_conversation_tokens(messages: list[ConversationMessage]) -> int:
    """Alias kept for backward compatibility."""
    return estimate_message_tokens(messages)


def _vision_token_budget_per_image() -> int:
    raw = os.environ.get("OPENHARNESS_IMAGE_TOKEN_ESTIMATE", "").strip()
    if raw:
        try:
            return max(64, int(raw))
        except ValueError:
            log.warning("Ignoring invalid OPENHARNESS_IMAGE_TOKEN_ESTIMATE=%r", raw)
    return _DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE


def _replace_images_with_compaction_placeholders(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Strip image payloads from summarizer-only compact requests."""
    replaced: list[ConversationMessage] = []
    for message in messages:
        next_content: list[ContentBlock] = []
        changed = False
        for block in message.content:
            if isinstance(block, ImageBlock):
                changed = True
                label = block.source_path.strip() or "inline"
                next_content.append(
                    TextBlock(
                        text=f"[Image omitted from compaction summarization; source: {label}.]\n"
                    )
                )
            else:
                next_content.append(block)
        if changed:
            replaced.append(message.model_copy(update={"content": next_content}))
        else:
            replaced.append(message)
    return replaced


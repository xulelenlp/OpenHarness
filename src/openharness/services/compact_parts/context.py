"""Context collapse and prompt-round grouping helpers."""

from __future__ import annotations

from openharness.engine.messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
)
from openharness.services.compact_parts.attachments import _split_preserving_tool_pairs
from openharness.services.compact_parts.constants import (
    CONTEXT_COLLAPSE_HEAD_CHARS,
    CONTEXT_COLLAPSE_TAIL_CHARS,
    CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT,
    PTL_RETRY_MARKER,
)
from openharness.services.compact_parts.tokens import estimate_message_tokens


def _group_messages_by_prompt_round(
    messages: list[ConversationMessage],
) -> list[list[ConversationMessage]]:
    groups: list[list[ConversationMessage]] = []
    current: list[ConversationMessage] = []
    for message in messages:
        starts_new_round = (
            message.role == "user"
            and not any(isinstance(block, ToolResultBlock) for block in message.content)
            and bool(message.text.strip())
        )
        if starts_new_round and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def _collapse_text(text: str) -> str:
    if len(text) <= CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT:
        return text
    omitted = len(text) - CONTEXT_COLLAPSE_HEAD_CHARS - CONTEXT_COLLAPSE_TAIL_CHARS
    head = text[:CONTEXT_COLLAPSE_HEAD_CHARS].rstrip()
    tail = text[-CONTEXT_COLLAPSE_TAIL_CHARS:].lstrip()
    return f"{head}\n...[collapsed {omitted} chars]...\n{tail}"


def try_context_collapse(
    messages: list[ConversationMessage],
    *,
    preserve_recent: int,
) -> list[ConversationMessage] | None:
    """Deterministically shrink oversized text blocks before full compact."""
    if len(messages) <= preserve_recent + 2:
        return None

    older, newer = _split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)
    changed = False
    collapsed_older: list[ConversationMessage] = []
    for message in older:
        new_blocks: list[ContentBlock] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                collapsed = _collapse_text(block.text)
                if collapsed != block.text:
                    changed = True
                new_blocks.append(TextBlock(text=collapsed))
            elif isinstance(block, ToolResultBlock):
                collapsed = _collapse_text(block.content)
                if collapsed != block.content:
                    changed = True
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=collapsed,
                        is_error=block.is_error,
                    )
                )
            else:
                new_blocks.append(block)
        collapsed_older.append(ConversationMessage(role=message.role, content=new_blocks))

    if not changed:
        return None

    result = [*collapsed_older, *newer]
    if estimate_message_tokens(result) >= estimate_message_tokens(messages):
        return None
    return result


def truncate_head_for_ptl_retry(
    messages: list[ConversationMessage],
) -> list[ConversationMessage] | None:
    """Drop the oldest prompt rounds when the compact request itself is too large."""
    groups = _group_messages_by_prompt_round(messages)
    if len(groups) < 2:
        return None

    drop_count = max(1, len(groups) // 5)
    drop_count = min(drop_count, len(groups) - 1)
    retained = [message for group in groups[drop_count:] for message in group]
    if not retained:
        return None
    if retained[0].role == "assistant":
        return [ConversationMessage.from_user_text(PTL_RETRY_MARKER), *retained]
    return retained

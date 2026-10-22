# ruff: noqa: F403, F405
"""Microcompaction and session-memory compaction."""

from __future__ import annotations

import logging

from openharness.engine.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from openharness.services.compact_parts.attachments import (
    _build_compact_attachments,
    _extract_attachment_paths,
    _extract_discovered_tools,
    _finalize_compaction_result,
    _split_preserving_tool_pairs,
    create_compact_boundary_message,
)
from openharness.services.compact_parts.constants import *
from openharness.services.compact_parts.models import CompactionResult
from openharness.services.compact_parts.tokens import estimate_message_tokens
from openharness.services.token_estimation import estimate_tokens
from openharness.services.tool_outputs import is_microcompactable_tool_result

log = logging.getLogger(__name__)


def _collect_compactable_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    """Walk messages and collect tool_use IDs whose results are compactable."""
    ordered_ids: list[str] = []
    tool_names: dict[str, str] = {}
    result_content: dict[str, str] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                ordered_ids.append(block.id)
                tool_names[block.id] = block.name
            elif isinstance(block, ToolResultBlock):
                result_content[block.tool_use_id] = block.content
    return [
        tool_id
        for tool_id in ordered_ids
        if tool_names.get(tool_id, "") in COMPACTABLE_TOOLS
        or is_microcompactable_tool_result(
            tool_names.get(tool_id, ""),
            result_content.get(tool_id, ""),
        )
    ]


def microcompact_messages(
    messages: list[ConversationMessage],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[ConversationMessage], int]:
    """Clear old compactable tool results, keeping the most recent *keep_recent*.

    This is the cheap first pass — no LLM call required. Tool result content
    is replaced with :data:`TIME_BASED_MC_CLEARED_MESSAGE`.

    Returns:
        (messages, tokens_saved) — messages are mutated in place for efficiency.
    """
    keep_recent = max(1, keep_recent)  # never clear ALL results
    all_ids = _collect_compactable_tool_ids(messages)

    if len(all_ids) <= keep_recent:
        return messages, 0

    keep_set = set(all_ids[-keep_recent:])
    clear_set = set(all_ids) - keep_set

    tokens_saved = 0
    for msg in messages:
        if msg.role != "user":
            continue
        new_content: list[ContentBlock] = []
        for block in msg.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.tool_use_id in clear_set
                and block.content != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens_saved += estimate_tokens(block.content)
                new_content.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=TIME_BASED_MC_CLEARED_MESSAGE,
                        is_error=block.is_error,
                    )
                )
            else:
                new_content.append(block)
        msg.content = new_content

    if tokens_saved > 0:
        log.info("Microcompact cleared %d tool results, saved ~%d tokens", len(clear_set), tokens_saved)

    return messages, tokens_saved


def _summarize_message_for_memory(message: ConversationMessage) -> str:
    text = " ".join(message.text.split())
    if text:
        text = text[:160]
        return f"{message.role}: {text}"
    tool_uses = [block.name for block in message.tool_uses]
    if tool_uses:
        return f"{message.role}: tool calls -> {', '.join(tool_uses[:4])}"
    if any(isinstance(block, ToolResultBlock) for block in message.content):
        return f"{message.role}: tool results returned"
    return f"{message.role}: [non-text content]"


def _build_session_memory_message(messages: list[ConversationMessage]) -> ConversationMessage | None:
    lines: list[str] = []
    total_chars = 0
    for message in messages:
        line = _summarize_message_for_memory(message)
        if not line:
            continue
        projected = total_chars + len(line) + 1
        if lines and (len(lines) >= SESSION_MEMORY_MAX_LINES or projected >= SESSION_MEMORY_MAX_CHARS):
            lines.append("... earlier context condensed ...")
            break
        lines.append(line)
        total_chars = projected
    if not lines:
        return None
    body = "\n".join(lines)
    return ConversationMessage.from_user_text(
        "Session memory summary from earlier in this conversation:\n" + body
    )


def _build_file_session_memory_message(metadata: dict[str, Any] | None) -> ConversationMessage | None:
    """Build a compaction message from the persisted session-memory file."""

    if not metadata:
        return None
    path = metadata.get("session_memory_path")
    if not path:
        return None
    try:
        from openharness.services.session_memory import (
            get_session_memory_content,
            session_memory_to_compact_text,
        )

        text = session_memory_to_compact_text(get_session_memory_content(str(path)))
    except Exception:
        return None
    if not text.strip():
        return None
    return ConversationMessage.from_user_text(text)


def try_session_memory_compaction(
    messages: list[ConversationMessage],
    *,
    preserve_recent: int = SESSION_MEMORY_KEEP_RECENT,
    trigger: CompactTrigger = "auto",
    metadata: dict[str, Any] | None = None,
) -> CompactionResult | None:
    """Cheap deterministic compaction for long chats before full LLM compaction."""
    if len(messages) <= preserve_recent + 4:
        return None
    older, newer = _split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)
    file_summary_message = _build_file_session_memory_message(metadata)
    summary_message = file_summary_message or _build_session_memory_message(older)
    if summary_message is None:
        return None
    provisional = [summary_message, *newer]
    if (
        estimate_message_tokens(provisional) >= estimate_message_tokens(messages)
        and len(provisional) >= len(messages)
    ):
        return None
    compact_metadata = {
        "trigger": trigger,
        "compact_kind": "session_memory",
        "pre_compact_message_count": len(messages),
        "pre_compact_token_count": estimate_message_tokens(messages),
        "preserve_recent": preserve_recent,
        "used_session_memory": True,
        "used_file_session_memory": file_summary_message is not None,
        "pre_compact_discovered_tools": _extract_discovered_tools(older),
        "attachments": _extract_attachment_paths(older),
    }
    result = CompactionResult(
        trigger=trigger,
        compact_kind="session_memory",
        boundary_marker=create_compact_boundary_message(compact_metadata),
        summary_messages=[summary_message],
        messages_to_keep=list(newer),
        attachments=_build_compact_attachments(older, metadata=metadata),
        hook_results=[],
        compact_metadata=compact_metadata,
    )
    return _finalize_compaction_result(result)

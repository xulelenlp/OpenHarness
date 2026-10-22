# ruff: noqa: F401, F403, F405
"""Conversation compaction — microcompact and full LLM-based summarization.

Faithfully translated from Claude Code's compaction system:
- Microcompact: clear old tool result content to reduce token count cheaply
- Full compact: call the LLM to produce a structured summary of older messages
- Auto-compact: trigger compaction automatically when token count exceeds threshold
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from openharness.engine.messages import (
    ContentBlock,
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from openharness.engine.stream_events import CompactProgressEvent
from openharness.hooks import HookEvent, HookExecutor
from openharness.services.compact_parts.attachments import *
from openharness.services.compact_parts.attachments import (
    _build_compact_attachments,
    _build_passthrough_compaction_result,
    _create_hook_attachments,
    _extract_attachment_paths,
    _extract_discovered_tools,
    _finalize_compaction_result,
    _metadata_has_checkpoint,
    _sanitize_compaction_segments,
    _split_preserving_tool_pairs,
)
from openharness.services.compact_parts.constants import *
from openharness.services.compact_parts.context import *
from openharness.services.compact_parts.context import (
    _collapse_text,
    _group_messages_by_prompt_round,
)
from openharness.services.compact_parts.metadata import _emit_progress, _record_compact_checkpoint
from openharness.services.compact_parts.micro import *
from openharness.services.compact_parts.models import CompactAttachment, CompactionResult
from openharness.services.compact_parts.prompts import *
from openharness.services.compact_parts.state import *
from openharness.services.compact_parts.tokens import (
    _replace_images_with_compaction_placeholders,
    _vision_token_budget_per_image,
    estimate_conversation_tokens,
    estimate_message_tokens,
)
from openharness.services.errors import is_prompt_too_long_error
from openharness.services.token_estimation import estimate_tokens
from openharness.services.tool_outputs import is_microcompactable_tool_result

log = logging.getLogger(__name__)

# Backwards-compatible private alias for tests and callers.
_is_prompt_too_long_error = is_prompt_too_long_error


# ---------------------------------------------------------------------------
# Full compact execution (calls the LLM)
# ---------------------------------------------------------------------------

async def compact_conversation(
    messages: list[ConversationMessage],
    *,
    api_client: Any,
    model: str,
    system_prompt: str = "",
    preserve_recent: int = 6,
    custom_instructions: str | None = None,
    suppress_follow_up: bool = True,
    trigger: CompactTrigger = "manual",
    progress_callback: CompactProgressCallback | None = None,
    emit_hooks_start: bool = True,
    hook_executor: HookExecutor | None = None,
    carryover_metadata: dict[str, Any] | None = None,
) -> CompactionResult:
    """Compact messages by calling the LLM to produce a summary.

    1. Microcompact first (cheap token reduction).
    2. Split into older (to summarize) and recent (to preserve).
    3. Call the LLM with the compact prompt to get a structured summary.
    4. Replace older messages with the summary + preserved recent messages.

    Args:
        messages: The full conversation history.
        api_client: An ``AnthropicApiClient`` or compatible for the summary call.
        model: Model ID to use for the summary.
        system_prompt: System prompt for the summary call.
        preserve_recent: Number of recent messages to keep verbatim.
        custom_instructions: Optional extra instructions for the summary prompt.
        suppress_follow_up: If True, instruct the model not to ask follow-ups.

    Returns:
        Structured compaction result that can be rebuilt into post-compact messages.
    """
    from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest

    if len(messages) <= preserve_recent:
        return _build_passthrough_compaction_result(
            messages,
            trigger=trigger,
            compact_kind="full",
            metadata={"reason": "conversation already within preserve_recent window"},
        )

    # Step 1: microcompact to reduce tokens cheaply
    messages, tokens_freed = microcompact_messages(messages, keep_recent=DEFAULT_KEEP_RECENT)

    pre_compact_tokens = estimate_message_tokens(messages)
    log.info("Compacting conversation: %d messages, ~%d tokens", len(messages), pre_compact_tokens)

    # Step 2: split into older (summarize) and newer (preserve)
    older, newer = _split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)

    # Step 3: build compact request — send older messages + compact prompt
    compact_prompt = get_compact_prompt(custom_instructions)
    compact_messages = list(older) + [ConversationMessage.from_user_text(compact_prompt)]
    attachment_paths = _extract_attachment_paths(older)
    discovered_tools = _extract_discovered_tools(older)
    hook_payload = {
        "event": HookEvent.PRE_COMPACT.value,
        "trigger": trigger,
        "model": model,
        "message_count": len(messages),
        "token_count": pre_compact_tokens,
        "preserve_recent": preserve_recent,
        "attachments": attachment_paths,
        "discovered_tools": discovered_tools,
        **(carryover_metadata or {}),
    }
    start_checkpoint = _record_compact_checkpoint(
        carryover_metadata,
        checkpoint="compact_prepare",
        trigger=trigger,
        message_count=len(messages),
        token_count=pre_compact_tokens,
        details={
            "preserve_recent": preserve_recent,
            "attachments": attachment_paths,
            "discovered_tools": discovered_tools,
        },
    )

    if emit_hooks_start:
        await _emit_progress(
            progress_callback,
            phase="hooks_start",
            trigger=trigger,
            message="Preparing conversation compaction.",
            checkpoint="compact_hooks_start",
            metadata=start_checkpoint,
        )
    if hook_executor is not None:
        hook_result = await hook_executor.execute(HookEvent.PRE_COMPACT, hook_payload)
        if hook_result.blocked:
            reason = hook_result.reason or "pre-compact hook blocked compaction"
            failed_checkpoint = _record_compact_checkpoint(
                carryover_metadata,
                checkpoint="compact_failed",
                trigger=trigger,
                message_count=len(messages),
                token_count=pre_compact_tokens,
                details={"reason": reason},
            )
            await _emit_progress(
                progress_callback,
                phase="compact_failed",
                trigger=trigger,
                message=reason,
                checkpoint="compact_failed",
                metadata=failed_checkpoint,
            )
            return _build_passthrough_compaction_result(
                messages,
                trigger=trigger,
                compact_kind="full",
                metadata={"reason": reason},
            )
    compact_start_checkpoint = _record_compact_checkpoint(
        carryover_metadata,
        checkpoint="compact_start",
        trigger=trigger,
        message_count=len(messages),
        token_count=pre_compact_tokens,
        details={"preserve_recent": preserve_recent},
    )
    await _emit_progress(
        progress_callback,
        phase="compact_start",
        trigger=trigger,
        message="Compacting conversation memory.",
        checkpoint="compact_start",
        metadata=compact_start_checkpoint,
    )

    summary_text = ""
    messages_to_summarize = compact_messages
    retry_messages = messages_to_summarize
    ptl_retries = 0

    async def _collect_summary(summary_request_messages: list[ConversationMessage]) -> str:
        collected = ""
        summary_request_messages = _replace_images_with_compaction_placeholders(
            summary_request_messages
        )
        stream = api_client.stream_message(
            ApiMessageRequest(
                model=model,
                messages=summary_request_messages,
                system_prompt=system_prompt or "You are a conversation summarizer.",
                max_tokens=MAX_OUTPUT_TOKENS_FOR_SUMMARY,
                tools=[],  # no tools for compact call
            )
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if not hasattr(stream, "__aiter__"):
            raise RuntimeError("Compaction client did not provide a streaming response.")
        async for event in stream:
            if isinstance(event, ApiMessageCompleteEvent):
                collected = event.message.text
        if collected.strip():
            return collected
        raise RuntimeError(ERROR_MESSAGE_INCOMPLETE_RESPONSE)

    for attempt in range(1, MAX_COMPACT_STREAMING_RETRIES + 2):
        try:
            summary_text = await asyncio.wait_for(
                _collect_summary(retry_messages),
                timeout=COMPACT_TIMEOUT_SECONDS,
            )
            break
        except Exception as exc:
            if _is_prompt_too_long_error(exc) and ptl_retries < MAX_PTL_RETRIES:
                truncated = truncate_head_for_ptl_retry(retry_messages[:-1])
                if truncated:
                    ptl_retries += 1
                    retry_messages = [*truncated, retry_messages[-1]]
                    await _emit_progress(
                        progress_callback,
                        phase="compact_retry",
                        trigger=trigger,
                        message="Compaction prompt was too large; retrying with older context trimmed.",
                        attempt=ptl_retries,
                        checkpoint="compact_retry_prompt_too_long",
                        metadata=_record_compact_checkpoint(
                            carryover_metadata,
                            checkpoint="compact_retry_prompt_too_long",
                            trigger=trigger,
                            message_count=len(retry_messages),
                            token_count=estimate_message_tokens(retry_messages),
                            attempt=ptl_retries,
                            details={"ptl_retries": ptl_retries},
                        ),
                    )
                    continue
            if attempt > MAX_COMPACT_STREAMING_RETRIES:
                await _emit_progress(
                    progress_callback,
                    phase="compact_failed",
                    trigger=trigger,
                    message=str(exc),
                    attempt=attempt,
                    checkpoint="compact_failed",
                    metadata=_record_compact_checkpoint(
                        carryover_metadata,
                        checkpoint="compact_failed",
                        trigger=trigger,
                        message_count=len(retry_messages),
                        token_count=estimate_message_tokens(retry_messages),
                        attempt=attempt,
                        details={"reason": str(exc)},
                    ),
                )
                raise
            await _emit_progress(
                progress_callback,
                phase="compact_retry",
                trigger=trigger,
                message=str(exc),
                attempt=attempt,
                checkpoint="compact_retry",
                metadata=_record_compact_checkpoint(
                    carryover_metadata,
                    checkpoint="compact_retry",
                    trigger=trigger,
                    message_count=len(retry_messages),
                    token_count=estimate_message_tokens(retry_messages),
                    attempt=attempt,
                    details={"reason": str(exc)},
                ),
            )

    if not summary_text:
        await _emit_progress(
            progress_callback,
            phase="compact_failed",
            trigger=trigger,
            message=ERROR_MESSAGE_INCOMPLETE_RESPONSE,
            checkpoint="compact_failed",
            metadata=_record_compact_checkpoint(
                carryover_metadata,
                checkpoint="compact_failed",
                trigger=trigger,
                message_count=len(messages),
                token_count=pre_compact_tokens,
                details={"reason": ERROR_MESSAGE_INCOMPLETE_RESPONSE},
            ),
        )
        log.warning("Compact summary was empty — returning original messages")
        return _build_passthrough_compaction_result(
            messages,
            trigger=trigger,
            compact_kind="full",
            metadata={"reason": ERROR_MESSAGE_INCOMPLETE_RESPONSE},
        )

    # Step 4: build the new message list
    summary_content = build_compact_summary_message(
        summary_text,
        suppress_follow_up=suppress_follow_up,
        recent_preserved=len(newer) > 0,
    )
    summary_msg = ConversationMessage.from_user_text(summary_content)
    initial_post_compact_tokens = estimate_message_tokens([summary_msg, *newer])
    if hook_executor is not None:
        post_hook_result = await hook_executor.execute(
            HookEvent.POST_COMPACT,
            {
                "event": HookEvent.POST_COMPACT.value,
                "trigger": trigger,
                "model": model,
                "pre_compact_message_count": len(messages),
                "post_compact_message_count": len(newer) + 1,
                "pre_compact_tokens": pre_compact_tokens,
                "post_compact_tokens": initial_post_compact_tokens,
                "attachments": attachment_paths,
                "discovered_tools": discovered_tools,
                **(carryover_metadata or {}),
            },
        )
        hook_note = post_hook_result.reason or "\n".join(
            result.output.strip()
            for result in post_hook_result.results
            if result.output.strip()
        )
        hook_attachments = _create_hook_attachments(hook_note)
    else:
        hook_attachments = []

    compact_metadata = {
        "trigger": trigger,
        "compact_kind": "full",
        "pre_compact_message_count": len(messages),
        "pre_compact_token_count": pre_compact_tokens,
        "preserve_recent": preserve_recent,
        "tokens_freed_by_microcompact": tokens_freed,
        "pre_compact_discovered_tools": discovered_tools,
        "used_head_truncation_retry": ptl_retries > 0,
        "used_context_collapse": _metadata_has_checkpoint(carryover_metadata, "query_context_collapse_end"),
        "used_session_memory": False,
        "retry_attempts": max(0, attempt - 1 if "attempt" in locals() else 0),
        "attachments": attachment_paths,
    }
    if carryover_metadata is not None:
        checkpoints = carryover_metadata.get("compact_checkpoints")
        if isinstance(checkpoints, list):
            compact_metadata["compact_checkpoints"] = checkpoints
        compact_last = carryover_metadata.get("compact_last")
        if isinstance(compact_last, dict):
            compact_metadata["compact_last"] = compact_last

    compaction_result = CompactionResult(
        trigger=trigger,
        compact_kind="full",
        boundary_marker=create_compact_boundary_message(compact_metadata),
        summary_messages=[summary_msg],
        messages_to_keep=list(newer),
        attachments=_build_compact_attachments(older, metadata=carryover_metadata),
        hook_results=hook_attachments,
        compact_metadata=compact_metadata,
    )
    compaction_result = _finalize_compaction_result(compaction_result)
    post_compact_messages = build_post_compact_messages(compaction_result)
    post_compact_tokens = estimate_message_tokens(post_compact_messages)
    compaction_result.compact_metadata["post_compact_message_count"] = len(post_compact_messages)
    compaction_result.compact_metadata["post_compact_token_count"] = post_compact_tokens
    compaction_result.boundary_marker = create_compact_boundary_message(compaction_result.compact_metadata)
    log.info(
        "Compaction done: %d -> %d messages, ~%d -> ~%d tokens (saved ~%d)",
        len(messages), len(post_compact_messages),
        pre_compact_tokens, post_compact_tokens,
        pre_compact_tokens - post_compact_tokens,
    )
    await _emit_progress(
        progress_callback,
        phase="compact_end",
        trigger=trigger,
        message="Conversation compaction complete.",
        checkpoint="compact_end",
        metadata=_record_compact_checkpoint(
            carryover_metadata,
            checkpoint="compact_end",
            trigger=trigger,
            message_count=len(post_compact_messages),
            token_count=post_compact_tokens,
            details={
                "pre_compact_message_count": len(messages),
                "post_compact_message_count": len(post_compact_messages),
                "pre_compact_tokens": pre_compact_tokens,
                "post_compact_tokens": post_compact_tokens,
                "tokens_saved": pre_compact_tokens - post_compact_tokens,
                "attachments": attachment_paths,
                "discovered_tools": discovered_tools,
            },
        ),
    )
    return compaction_result


# ---------------------------------------------------------------------------
# Auto-compact integration (called from query loop)
# ---------------------------------------------------------------------------

async def auto_compact_if_needed(
    messages: list[ConversationMessage],
    *,
    api_client: Any,
    model: str,
    system_prompt: str = "",
    state: AutoCompactState,
    preserve_recent: int = 6,
    progress_callback: CompactProgressCallback | None = None,
    force: bool = False,
    trigger: CompactTrigger = "auto",
    hook_executor: HookExecutor | None = None,
    carryover_metadata: dict[str, Any] | None = None,
    context_window_tokens: int | None = None,
    auto_compact_threshold_tokens: int | None = None,
) -> tuple[list[ConversationMessage], bool]:
    """Check if auto-compact should fire, and if so, compact.

    Call this at the start of each query loop turn.

    Returns:
        (messages, was_compacted) — if compacted, messages is the new list.
    """
    if not force and not should_autocompact(
        messages,
        model,
        state,
        context_window_tokens=context_window_tokens,
        auto_compact_threshold_tokens=auto_compact_threshold_tokens,
    ):
        return messages, False

    log.info("Auto-compact triggered (failures=%d)", state.consecutive_failures)
    _record_compact_checkpoint(
        carryover_metadata,
        checkpoint=f"query_{trigger}_triggered",
        trigger=trigger,
        message_count=len(messages),
        token_count=estimate_message_tokens(messages),
        details={"consecutive_failures": state.consecutive_failures},
    )

    # Try microcompact first — may be enough
    messages, tokens_freed = microcompact_messages(messages)
    _record_compact_checkpoint(
        carryover_metadata,
        checkpoint="query_microcompact_end",
        trigger=trigger,
        message_count=len(messages),
        token_count=estimate_message_tokens(messages),
        details={"tokens_freed": tokens_freed},
    )
    if tokens_freed > 0 and not should_autocompact(
        messages,
        model,
        state,
        context_window_tokens=context_window_tokens,
        auto_compact_threshold_tokens=auto_compact_threshold_tokens,
    ):
        log.info("Microcompact freed ~%d tokens, auto-compact no longer needed", tokens_freed)
        return messages, True

    context_collapsed = try_context_collapse(messages, preserve_recent=preserve_recent)
    if context_collapsed is not None:
        await _emit_progress(
            progress_callback,
            phase="context_collapse_start",
            trigger=trigger,
            message="Collapsing oversized context before full compaction.",
            checkpoint="query_context_collapse_start",
            metadata=_record_compact_checkpoint(
                carryover_metadata,
                checkpoint="query_context_collapse_start",
                trigger=trigger,
                message_count=len(messages),
                token_count=estimate_message_tokens(messages),
            ),
        )
        messages = context_collapsed
        await _emit_progress(
            progress_callback,
            phase="context_collapse_end",
            trigger=trigger,
            message="Context collapse complete.",
            checkpoint="query_context_collapse_end",
            metadata=_record_compact_checkpoint(
                carryover_metadata,
                checkpoint="query_context_collapse_end",
                trigger=trigger,
                message_count=len(messages),
                token_count=estimate_message_tokens(messages),
            ),
        )
        if not force and not should_autocompact(
            messages,
            model,
            state,
            context_window_tokens=context_window_tokens,
            auto_compact_threshold_tokens=auto_compact_threshold_tokens,
        ):
            return messages, True

    session_memory = try_session_memory_compaction(
        messages,
        preserve_recent=max(preserve_recent, SESSION_MEMORY_KEEP_RECENT),
        trigger=trigger,
        metadata=carryover_metadata,
    )
    if session_memory is not None:
        await _emit_progress(
            progress_callback,
            phase="session_memory_start",
            trigger=trigger,
            message="Condensing earlier conversation into session memory.",
            checkpoint="query_session_memory_start",
            metadata=_record_compact_checkpoint(
                carryover_metadata,
                checkpoint="query_session_memory_start",
                trigger=trigger,
                message_count=len(messages),
                token_count=estimate_message_tokens(messages),
            ),
        )
        await _emit_progress(
            progress_callback,
            phase="session_memory_end",
            trigger=trigger,
            message="Session memory condensation complete.",
            checkpoint="query_session_memory_end",
            metadata=_record_compact_checkpoint(
                carryover_metadata,
                checkpoint="query_session_memory_end",
                trigger=trigger,
                message_count=len(build_post_compact_messages(session_memory)),
                token_count=estimate_message_tokens(build_post_compact_messages(session_memory)),
            ),
        )
        state.compacted = True
        state.turn_counter += 1
        state.turn_id = uuid4().hex
        state.consecutive_failures = 0
        return build_post_compact_messages(session_memory), True

    # Full compact needed
    try:
        result = await compact_conversation(
            messages,
            api_client=api_client,
            model=model,
            system_prompt=system_prompt,
            preserve_recent=preserve_recent,
            suppress_follow_up=True,
            trigger=trigger,
            progress_callback=progress_callback,
            hook_executor=hook_executor,
            carryover_metadata=carryover_metadata,
        )
        state.compacted = True
        state.turn_counter += 1
        state.turn_id = uuid4().hex
        state.consecutive_failures = 0
        return build_post_compact_messages(result), True
    except Exception as exc:
        state.consecutive_failures += 1
        _record_compact_checkpoint(
            carryover_metadata,
            checkpoint=f"query_{trigger}_failed",
            trigger=trigger,
            message_count=len(messages),
            token_count=estimate_message_tokens(messages),
            details={"reason": str(exc), "consecutive_failures": state.consecutive_failures},
        )
        log.error(
            "Auto-compact failed (attempt %d/%d): %s",
            state.consecutive_failures,
            MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
            exc,
        )
        return messages, False


# ---------------------------------------------------------------------------
# Legacy compat
# ---------------------------------------------------------------------------

def summarize_messages(
    messages: list[ConversationMessage],
    *,
    max_messages: int = 8,
) -> str:
    """Produce a compact textual summary of recent messages (legacy)."""
    selected = messages[-max_messages:]
    lines: list[str] = []
    for message in selected:
        text = message.text.strip()
        if not text:
            continue
        lines.append(f"{message.role}: {text[:300]}")
    return "\n".join(lines)


def compact_messages(
    messages: list[ConversationMessage],
    *,
    preserve_recent: int = 6,
) -> list[ConversationMessage]:
    """Replace older conversation history with a synthetic summary (legacy)."""
    if len(messages) <= preserve_recent:
        return sanitize_conversation_messages(list(messages))
    older, newer = _split_preserving_tool_pairs(messages, preserve_recent=preserve_recent)
    summary = summarize_messages(older)
    if not summary:
        return list(newer)
    return sanitize_conversation_messages([
        ConversationMessage(
            role="user",
            content=[TextBlock(text=f"[conversation summary]\n{summary}")],
        ),
        *newer,
    ])


__all__ = [
    "AUTO_COMPACT_BUFFER_TOKENS",
    "AutoCompactState",
    "CompactAttachment",
    "CompactionResult",
    "COMPACTABLE_TOOLS",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "auto_compact_if_needed",
    "build_post_compact_messages",
    "build_compact_summary_message",
    "compact_conversation",
    "compact_messages",
    "create_compact_boundary_message",
    "estimate_conversation_tokens",
    "estimate_message_tokens",
    "format_compact_summary",
    "get_autocompact_threshold",
    "get_compact_prompt",
    "microcompact_messages",
    "should_autocompact",
    "summarize_messages",
]

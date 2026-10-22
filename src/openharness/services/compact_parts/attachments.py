# ruff: noqa: F403, F405
"""Structured compact attachments."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from openharness.engine.messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from openharness.services.compact_parts.constants import *
from openharness.services.compact_parts.metadata import _sanitize_metadata
from openharness.services.compact_parts.models import CompactAttachment, CompactionResult
from openharness.services.compact_parts.tokens import estimate_message_tokens

log = logging.getLogger(__name__)

def _extract_attachment_paths(messages: list[ConversationMessage]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    path_pattern = re.compile(r"path:\s*([^)\\n]+)")
    attachment_pattern = re.compile(r"\[attachment:\s*([^\]]+)\]")
    for message in messages:
        for block in message.content:
            if isinstance(block, ImageBlock) and block.source_path:
                path = str(Path(block.source_path).expanduser())
                if path not in seen:
                    seen.add(path)
                    found.append(path)
            elif isinstance(block, TextBlock):
                for match in path_pattern.findall(block.text):
                    path = match.strip()
                    if path and path not in seen:
                        seen.add(path)
                        found.append(path)
                for match in attachment_pattern.findall(block.text):
                    path = match.strip()
                    if path and "download failed" not in path and path not in seen:
                        seen.add(path)
                        found.append(path)
            if len(found) >= MAX_COMPACT_ATTACHMENTS:
                return found
    return found


def _extract_discovered_tools(messages: list[ConversationMessage]) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for tool_use in message.tool_uses:
            if tool_use.name and tool_use.name not in seen:
                seen.add(tool_use.name)
                discovered.append(tool_use.name)
            if len(discovered) >= MAX_DISCOVERED_TOOLS:
                return discovered
    return discovered


def _create_attachment(kind: str, title: str, lines: list[str], *, metadata: dict[str, Any] | None = None) -> CompactAttachment | None:
    filtered = [line.rstrip() for line in lines if line and line.strip()]
    if not filtered:
        return None
    return CompactAttachment(
        kind=kind,
        title=title,
        body="\n".join(filtered),
        metadata=_sanitize_metadata(metadata or {}),
    )


def render_compact_attachment(attachment: CompactAttachment) -> ConversationMessage:
    """Serialize a structured compact attachment into a conversation message."""
    header = f"[Compact attachment: {attachment.kind}] {attachment.title}".strip()
    text = f"{header}\n{attachment.body}".strip()
    return ConversationMessage.from_user_text(text)


def create_compact_boundary_message(metadata: dict[str, Any]) -> ConversationMessage:
    """Create a boundary marker message for post-compact conversation rebuild."""
    lines = [
        "[Compact boundary marker]",
        "Earlier conversation was compacted. Use the summary and preserved assets below as the continuity boundary.",
    ]
    trigger = str(metadata.get("trigger") or "").strip()
    compact_kind = str(metadata.get("compact_kind") or "").strip()
    pre_messages = metadata.get("pre_compact_message_count")
    pre_tokens = metadata.get("pre_compact_token_count")
    post_messages = metadata.get("post_compact_message_count")
    post_tokens = metadata.get("post_compact_token_count")
    if trigger:
        lines.append(f"Trigger: {trigger}")
    if compact_kind:
        lines.append(f"Compaction kind: {compact_kind}")
    if pre_messages is not None or pre_tokens is not None:
        lines.append(
            "Pre-compact footprint: "
            f"messages={pre_messages if pre_messages is not None else 'unknown'}, "
            f"tokens={pre_tokens if pre_tokens is not None else 'unknown'}"
        )
    if post_messages is not None or post_tokens is not None:
        lines.append(
            "Post-compact footprint: "
            f"messages={post_messages if post_messages is not None else 'unknown'}, "
            f"tokens={post_tokens if post_tokens is not None else 'unknown'}"
        )
    anchor = str(metadata.get("preserved_segment_anchor") or "").strip()
    if anchor:
        lines.append(f"Preserved segment anchor: {anchor}")
    return ConversationMessage.from_user_text("\n".join(lines))


def build_post_compact_messages(result: CompactionResult) -> list[ConversationMessage]:
    """Rebuild the post-compact message list in Claude Code's ordering."""
    attachment_messages = [render_compact_attachment(attachment) for attachment in result.attachments]
    hook_messages = [render_compact_attachment(attachment) for attachment in result.hook_results]
    return [
        result.boundary_marker,
        *result.summary_messages,
        *result.messages_to_keep,
        *attachment_messages,
        *hook_messages,
    ]


def _boundary_crosses_tool_pair(previous: ConversationMessage, current: ConversationMessage) -> bool:
    """Return True when a preserve boundary would split a tool_use/result pair."""

    if previous.role != "assistant" or current.role != "user":
        return False
    pending_tool_ids = {block.id for block in previous.content if isinstance(block, ToolUseBlock)}
    if not pending_tool_ids:
        return False
    result_ids = {block.tool_use_id for block in current.content if isinstance(block, ToolResultBlock)}
    return bool(pending_tool_ids & result_ids)


def _split_preserving_tool_pairs(
    messages: list[ConversationMessage],
    *,
    preserve_recent: int,
) -> tuple[list[ConversationMessage], list[ConversationMessage]]:
    """Split older/newer segments without cutting through a tool_use/result pair.

    The preserved segment is also sanitized so trailing orphan tool_use blocks
    never survive the compaction boundary.
    """

    if len(messages) <= preserve_recent:
        return [], sanitize_conversation_messages(list(messages))

    split_index = max(0, len(messages) - preserve_recent)
    while split_index > 0 and _boundary_crosses_tool_pair(messages[split_index - 1], messages[split_index]):
        split_index -= 1

    older = list(messages[:split_index])
    newer = sanitize_conversation_messages(list(messages[split_index:]))
    return older, newer


def _sanitize_compaction_segments(result: CompactionResult) -> None:
    """Normalize summary+preserved messages into a provider-safe sequence."""

    if not result.summary_messages and not result.messages_to_keep:
        return
    combined = [*result.summary_messages, *result.messages_to_keep]
    sanitized = sanitize_conversation_messages(combined)
    summary_count = len(result.summary_messages)
    result.summary_messages = sanitized[:summary_count]
    result.messages_to_keep = sanitized[summary_count:]


def _create_recent_attachments_attachment_if_needed(
    attachment_paths: list[str],
) -> CompactAttachment | None:
    if not attachment_paths:
        return None
    return _create_attachment(
        "recent_attachments",
        "Recent local attachments",
        ["Keep these local attachment paths in working memory:"] + [f"- {path}" for path in attachment_paths],
        metadata={"paths": attachment_paths},
    )


def create_recent_files_attachment_if_needed(
    read_file_state: Any,
) -> CompactAttachment | None:
    if not isinstance(read_file_state, list) or not read_file_state:
        return None
    lines = ["Recently read files that may still matter:"]
    entries: list[dict[str, Any]] = []
    normalized_entries = [
        entry
        for entry in read_file_state
        if isinstance(entry, dict) and str(entry.get("path") or "").strip()
    ]
    normalized_entries.sort(
        key=lambda entry: float(entry.get("timestamp") or 0.0),
        reverse=True,
    )
    for entry in normalized_entries[:4]:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        span = str(entry.get("span") or "").strip()
        preview = str(entry.get("preview") or "").strip()
        timestamp = entry.get("timestamp")
        if not path:
            continue
        bullet = f"- {path}"
        if span:
            bullet += f" ({span})"
        lines.append(bullet)
        if preview:
            lines.append(f"  Preview: {preview}")
        entries.append({"path": path, "span": span, "preview": preview, "timestamp": timestamp})
    return _create_attachment("recent_files", "Recently read files", lines, metadata={"entries": entries})


def create_task_focus_attachment_if_needed(
    metadata: dict[str, Any],
) -> CompactAttachment | None:
    state = metadata.get("task_focus_state")
    if not isinstance(state, dict):
        return None
    goal = str(state.get("goal") or "").strip()
    recent_goals = [
        str(item).strip()
        for item in state.get("recent_goals", [])
        if str(item).strip()
    ]
    active_artifacts = [
        str(item).strip()
        for item in state.get("active_artifacts", [])
        if str(item).strip()
    ]
    verified_state = [
        str(item).strip()
        for item in state.get("verified_state", [])
        if str(item).strip()
    ]
    next_step = str(state.get("next_step") or "").strip()
    if not any((goal, recent_goals, active_artifacts, verified_state, next_step)):
        return None
    lines = ["Current working focus to preserve across compaction:"]
    if goal:
        lines.append(f"- Goal: {goal}")
    if recent_goals:
        lines.append("- Recent user goals that still matter:")
        lines.extend(f"  - {item}" for item in recent_goals[-3:])
    if active_artifacts:
        lines.append("- Active artifacts in play:")
        lines.extend(f"  - {item}" for item in active_artifacts[-5:])
    if verified_state:
        lines.append("- Verified state already established:")
        lines.extend(f"  - {item}" for item in verified_state[-4:])
    if next_step:
        lines.append(f"- Suggested next step: {next_step}")
    return _create_attachment(
        "task_focus",
        "Current working focus",
        lines,
        metadata={
            "goal": goal,
            "recent_goals": recent_goals[-3:],
            "active_artifacts": active_artifacts[-5:],
            "verified_state": verified_state[-4:],
            "next_step": next_step,
        },
    )


def create_recent_verified_work_attachment_if_needed(
    verified_work: Any,
) -> CompactAttachment | None:
    if not isinstance(verified_work, list) or not verified_work:
        return None
    entries = [str(entry).strip() for entry in verified_work[-8:] if str(entry).strip()]
    if not entries:
        return None
    return _create_attachment(
        "recent_verified_work",
        "Recently verified work",
        ["These steps or conclusions were explicitly verified before compaction:"] + [f"- {entry}" for entry in entries],
        metadata={"entries": entries},
    )


def create_plan_attachment_if_needed(metadata: dict[str, Any]) -> CompactAttachment | None:
    permission_mode = str(metadata.get("permission_mode") or "").strip().lower()
    if permission_mode != "plan":
        return None
    lines = [
        "Plan mode is still active for this session.",
        "Do not execute mutating tools until the user explicitly exits plan mode.",
    ]
    plan_summary = str(metadata.get("plan_summary") or "").strip()
    if plan_summary:
        lines.append(f"Current plan summary: {plan_summary}")
    return _create_attachment(
        "plan",
        "Plan mode context",
        lines,
        metadata={"permission_mode": permission_mode, "plan_summary": plan_summary},
    )


def create_invoked_skills_attachment_if_needed(
    invoked_skills: Any,
) -> CompactAttachment | None:
    if not isinstance(invoked_skills, list) or not invoked_skills:
        return None
    normalized = [str(skill).strip() for skill in invoked_skills[-8:] if str(skill).strip()]
    if not normalized:
        return None
    return _create_attachment(
        "invoked_skills",
        "Skills used earlier in the session",
        ["The following skills were invoked and may still shape the next step:", "- " + ", ".join(normalized)],
        metadata={"skills": normalized},
    )


def create_async_agent_attachment_if_needed(
    async_agent_state: Any,
) -> CompactAttachment | None:
    if not isinstance(async_agent_state, list) or not async_agent_state:
        return None
    entries = [str(entry).strip() for entry in async_agent_state[-6:] if str(entry).strip()]
    if not entries:
        return None
    return _create_attachment(
        "async_agents",
        "Async agent and background task state",
        ["Recent async-agent/background-task activity:"] + [f"- {entry}" for entry in entries],
        metadata={"entries": entries},
    )


def create_work_log_attachment_if_needed(
    recent_work_log: Any,
) -> CompactAttachment | None:
    if not isinstance(recent_work_log, list) or not recent_work_log:
        return None
    entries = [str(entry).strip() for entry in recent_work_log[-8:] if str(entry).strip()]
    if not entries:
        return None
    return _create_attachment(
        "recent_work_log",
        "Recent execution checkpoints",
        ["Recent work and verification steps taken in this session:"] + [f"- {entry}" for entry in entries],
        metadata={"entries": entries},
    )


def _create_hook_attachments(hook_note: str | None) -> list[CompactAttachment]:
    if not hook_note or not hook_note.strip():
        return []
    attachment = _create_attachment(
        "hook_results",
        "Compact hook notes",
        [hook_note.strip()],
        metadata={"note": hook_note.strip()},
    )
    return [attachment] if attachment is not None else []


def _build_compact_attachments(
    messages: list[ConversationMessage],
    *,
    metadata: dict[str, Any] | None,
) -> list[CompactAttachment]:
    metadata = metadata or {}
    attachments: list[CompactAttachment] = []
    attachment_paths = _extract_attachment_paths(messages)
    builders = [
        create_task_focus_attachment_if_needed(metadata),
        create_recent_verified_work_attachment_if_needed(metadata.get("recent_verified_work")),
        _create_recent_attachments_attachment_if_needed(attachment_paths),
        create_recent_files_attachment_if_needed(metadata.get("read_file_state")),
        create_plan_attachment_if_needed(metadata),
        create_invoked_skills_attachment_if_needed(metadata.get("invoked_skills")),
        create_async_agent_attachment_if_needed(metadata.get("async_agent_state")),
        create_work_log_attachment_if_needed(metadata.get("recent_work_log")),
    ]
    attachments.extend(attachment for attachment in builders if attachment is not None)
    return attachments


def _finalize_compaction_result(result: CompactionResult) -> CompactionResult:
    _sanitize_compaction_segments(result)
    messages = build_post_compact_messages(result)
    result.compact_metadata.setdefault("post_compact_message_count", len(messages))
    result.compact_metadata.setdefault("post_compact_token_count", estimate_message_tokens(messages))
    result.boundary_marker = create_compact_boundary_message(result.compact_metadata)
    return result


def _metadata_has_checkpoint(metadata: dict[str, Any] | None, checkpoint: str) -> bool:
    if metadata is None:
        return False
    checkpoints = metadata.get("compact_checkpoints")
    if not isinstance(checkpoints, list):
        return False
    return any(isinstance(entry, dict) and entry.get("checkpoint") == checkpoint for entry in checkpoints)


def _build_passthrough_compaction_result(
    messages: list[ConversationMessage],
    *,
    trigger: CompactTrigger,
    compact_kind: CompactionKind,
    metadata: dict[str, Any] | None = None,
) -> CompactionResult:
    compact_metadata = {
        "trigger": trigger,
        "compact_kind": compact_kind,
        "pre_compact_message_count": len(messages),
        "pre_compact_token_count": estimate_message_tokens(messages),
        **_sanitize_metadata(metadata or {}),
    }
    result = CompactionResult(
        trigger=trigger,
        compact_kind=compact_kind,
        boundary_marker=create_compact_boundary_message(compact_metadata),
        summary_messages=[],
        messages_to_keep=list(messages),
        attachments=[],
        hook_results=[],
        compact_metadata=compact_metadata,
    )
    return _finalize_compaction_result(result)

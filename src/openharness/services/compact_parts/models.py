"""Compact result data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openharness.engine.messages import ConversationMessage
from openharness.services.compact_parts.constants import CompactionKind, CompactTrigger


@dataclass
class CompactAttachment:
    """Structured compact asset carried across a compaction boundary."""

    kind: str
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompactionResult:
    """Structured compaction result, inspired by Claude Code's result shape."""

    trigger: CompactTrigger
    compact_kind: CompactionKind
    boundary_marker: ConversationMessage
    summary_messages: list[ConversationMessage]
    messages_to_keep: list[ConversationMessage]
    attachments: list[CompactAttachment]
    hook_results: list[CompactAttachment]
    compact_metadata: dict[str, Any] = field(default_factory=dict)



"""Shared constants and update type for the ohmo gateway runtime."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CHANNEL_THINKING_PHRASES = (
    "🤔 想一想…",
    "🧠 琢磨中…",
    "✨ 整理一下思路…",
    "🔎 看看这个…",
    "🪄 捋一捋线索…",
)

_CHANNEL_THINKING_PHRASES_EN = (
    "🤔 Thinking…",
    "🧠 Working through it…",
    "✨ Pulling the pieces together…",
    "🔎 Looking into it…",
    "🪄 Following the thread…",
)

_TEXT_PREVIEW_BYTES = 4096
_TEXT_PREVIEW_CHARS = 900
_BINARY_HEAD_BYTES = 32
_FINAL_REPLY_IMAGE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)[^\r\n`\"'<>|?*\x00]+?\.(?:png|jpe?g|webp|gif|bmp))",
    re.IGNORECASE,
)
_IMAGE_FALLBACK_NOTE = (
    "[Image attachment omitted because the active model does not support image input. "
    "Use the attachment paths and summaries above if needed.]"
)
_NO_GROUP_REQUEST = object()
_GROUP_TOOL_NAME = "ohmo_create_feishu_group"
_GROUP_AGENT_PROMPT_PREFIX = "The user invoked `/group` from a Feishu private chat."
_GROUP_AGENT_PROMPT_REQUEST_MARKER = "User /group request:"
_GROUP_METADATA_KEYS = (
    "task_focus_state",
    "recent_work_log",
    "recent_verified_work",
    "compact_checkpoints",
    "compact_last",
)


@dataclass(frozen=True)
class GatewayStreamUpdate:
    """One outbound update produced while processing a channel message."""

    kind: str
    text: str
    metadata: dict[str, object]
    media: list[str] | None = None

__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")]

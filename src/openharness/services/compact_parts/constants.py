"""Compact constants and shared type aliases."""

from __future__ import annotations

from typing import Awaitable, Callable, Literal

from openharness.engine.stream_events import CompactProgressEvent

# ---------------------------------------------------------------------------
# Constants (from Claude Code microCompact.ts / autoCompact.ts)
# ---------------------------------------------------------------------------

COMPACTABLE_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "bash",
    "grep",
    "glob",
    "web_search",
    "web_fetch",
    "edit_file",
    "write_file",
})

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Auto-compact thresholds
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
COMPACT_TIMEOUT_SECONDS = 25
MAX_COMPACT_STREAMING_RETRIES = 2
MAX_PTL_RETRIES = 3
SESSION_MEMORY_KEEP_RECENT = 12
SESSION_MEMORY_MAX_LINES = 48
SESSION_MEMORY_MAX_CHARS = 4_000
CONTEXT_COLLAPSE_TEXT_CHAR_LIMIT = 2_400
CONTEXT_COLLAPSE_HEAD_CHARS = 900
CONTEXT_COLLAPSE_TAIL_CHARS = 500
MAX_COMPACT_ATTACHMENTS = 6
MAX_DISCOVERED_TOOLS = 12

# Microcompact defaults
DEFAULT_KEEP_RECENT = 5
DEFAULT_GAP_THRESHOLD_MINUTES = 60

# Token estimation padding (conservative)
TOKEN_ESTIMATION_PADDING = 4 / 3
_DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE = 3_072

# Default context windows per model family
_DEFAULT_CONTEXT_WINDOW = 200_000
PTL_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"
ERROR_MESSAGE_INCOMPLETE_RESPONSE = "Compaction interrupted before a complete summary was returned."

CompactTrigger = Literal["auto", "manual", "reactive"]
CompactProgressCallback = Callable[[CompactProgressEvent], Awaitable[None]]
CompactionKind = Literal["full", "session_memory"]



__all__ = [name for name in globals() if not name.startswith("__")]

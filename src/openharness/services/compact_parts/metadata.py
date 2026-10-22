# ruff: noqa: F403, F405
"""Compaction checkpoint metadata helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from openharness.engine.stream_events import CompactProgressEvent
from openharness.services.compact_parts.constants import *
from openharness.services.errors import is_prompt_too_long_error

log = logging.getLogger(__name__)

def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_metadata(item) for item in value]
    return str(value)


def _record_compact_checkpoint(
    carryover_metadata: dict[str, Any] | None,
    *,
    checkpoint: str,
    trigger: CompactTrigger,
    message_count: int,
    token_count: int,
    attempt: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "checkpoint": checkpoint,
        "trigger": trigger,
        "message_count": message_count,
        "token_count": token_count,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    if details:
        payload.update(_sanitize_metadata(details))
    if carryover_metadata is not None:
        checkpoints = carryover_metadata.setdefault("compact_checkpoints", [])
        if isinstance(checkpoints, list):
            checkpoints.append(payload)
        carryover_metadata["compact_last"] = payload
    return payload


async def _emit_progress(
    callback: CompactProgressCallback | None,
    *,
    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ],
    trigger: CompactTrigger,
    message: str | None = None,
    attempt: int | None = None,
    checkpoint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    await callback(
        CompactProgressEvent(
            phase=phase,
            trigger=trigger,
            message=message,
            attempt=attempt,
            checkpoint=checkpoint,
            metadata=_sanitize_metadata(metadata) if metadata else None,
        )
    )


# Backwards-compatible private alias for tests and callers.
_is_prompt_too_long_error = is_prompt_too_long_error


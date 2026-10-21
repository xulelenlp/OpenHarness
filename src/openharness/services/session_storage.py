"""Session persistence helpers.

The storage format is shared by the core harness (per-project sessions) and
the ohmo personal agent (per-workspace sessions). The generic ``*_in``
functions take an explicit session directory; the public wrappers below keep
the historical project-scoped API.
"""

from __future__ import annotations

import json
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.config.paths import get_sessions_dir
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.utils.fs import atomic_write_text

_PERSISTED_TOOL_METADATA_KEYS = (
    "permission_mode",
    "read_file_state",
    "invoked_skills",
    "async_agent_state",
    "async_agent_tasks",
    "recent_work_log",
    "recent_verified_work",
    "task_focus_state",
    "compact_checkpoints",
    "compact_last",
)


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


def _persistable_tool_metadata(tool_metadata: dict[str, object] | None) -> dict[str, Any]:
    if not isinstance(tool_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in _PERSISTED_TOOL_METADATA_KEYS:
        if key in tool_metadata:
            payload[key] = _sanitize_metadata(tool_metadata[key])
    return payload


def session_key_latest_name(session_key: str) -> str:
    """Return the ``latest-<hash>.json`` filename for a session key."""
    return f"latest-{sha1(session_key.encode('utf-8')).hexdigest()[:12]}.json"


def get_session_key_latest_path(session_dir: str | Path, session_key: str) -> Path:
    """Return the per-session-key ``latest-<hash>.json`` path inside *session_dir*."""
    return Path(session_dir) / session_key_latest_name(session_key)


def _summary_from_messages(messages: list[ConversationMessage]) -> str:
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            return msg.text.strip()[:80]
    return ""


def _summary_from_payload(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", "")
    if isinstance(summary, str) and summary:
        return summary
    for msg in payload.get("messages", []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        texts = [
            block.get("text", "")
            for block in msg.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = " ".join(texts).strip()[:80]
        if joined:
            return joined
    return ""


def write_snapshot(
    session_dir: str | Path,
    *,
    cwd: str | Path,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    session_key: str | None = None,
    app: str = "",
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist a snapshot as ``latest.json``, ``session-<id>.json`` and (optionally) per-key latest."""
    directory = Path(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)

    payload: dict[str, Any] = {
        "session_id": sid,
        "cwd": str(Path(cwd).resolve()),
        "model": model,
        "system_prompt": system_prompt,
        "messages": [message.model_dump(mode="json") for message in messages],
        "usage": usage.model_dump(),
        "tool_metadata": _persistable_tool_metadata(tool_metadata),
        "created_at": now,
        "summary": _summary_from_messages(messages),
        "message_count": len(messages),
    }
    if app or session_key:
        payload["app"] = app or None
        payload["session_key"] = session_key
    data = json.dumps(payload, indent=2) + "\n"

    latest_path = directory / "latest.json"
    atomic_write_text(latest_path, data)
    atomic_write_text(directory / f"session-{sid}.json", data)
    if session_key:
        atomic_write_text(get_session_key_latest_path(directory, session_key), data)
    return latest_path


def _sanitize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted messages for forward compatibility."""
    raw_messages = payload.get("messages", [])
    if isinstance(raw_messages, list):
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(item) for item in raw_messages]
        )
        payload = dict(payload)
        payload["messages"] = [message.model_dump(mode="json") for message in messages]
        payload["message_count"] = len(messages)
    return payload


def load_snapshot_file(path: str | Path) -> dict[str, Any] | None:
    """Load and sanitize one snapshot file, or return ``None`` when unreadable."""
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _sanitize_snapshot_payload(payload)


def list_snapshots_in(
    session_dir: str | Path,
    *,
    limit: int = 20,
    include_latest: bool = False,
    latest_summary: str = "(latest session)",
) -> list[dict[str, Any]]:
    """List snapshot summaries from a directory, newest file first.

    ``include_latest`` keeps the historical project-level behaviour of
    surfacing ``latest.json`` when no ``session-<id>.json`` matches it.
    """
    directory = Path(session_dir)
    sessions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in sorted(directory.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("session_id", path.stem.replace("session-", ""))
        seen_ids.add(sid)
        sessions.append(
            {
                "session_id": sid,
                "summary": _summary_from_payload(data),
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            }
        )
        if len(sessions) >= limit:
            break

    latest_path = directory / "latest.json"
    if include_latest and latest_path.exists() and len(sessions) < limit:
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            sid = data.get("session_id", "latest")
            if sid not in seen_ids:
                sessions.append(
                    {
                        "session_id": sid,
                        "summary": _summary_from_payload(data) or latest_summary,
                        "message_count": data.get("message_count", len(data.get("messages", []))),
                        "model": data.get("model", ""),
                        "created_at": data.get("created_at", latest_path.stat().st_mtime),
                    }
                )
    return sessions


def load_snapshot_by_id_in(
    session_dir: str | Path,
    session_id: str,
    *,
    include_latest: bool = True,
) -> dict[str, Any] | None:
    """Load ``session-<id>.json``, optionally falling back to ``latest.json``."""
    directory = Path(session_dir)
    path = directory / f"session-{session_id}.json"
    if path.exists():
        return load_snapshot_file(path)
    if not include_latest:
        return None
    data = load_snapshot_file(directory / "latest.json")
    if data and (data.get("session_id") == session_id or session_id == "latest"):
        return data
    return None


def export_transcript(
    session_dir: str | Path,
    *,
    title: str,
    messages: list[ConversationMessage],
    include_tool_blocks: bool = True,
) -> Path:
    """Write ``transcript.md`` for a session and return its path."""
    directory = Path(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "transcript.md"
    parts = [f"# {title}"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
        if not include_tool_blocks:
            continue
        for block in message.tool_uses:
            parts.append(f"\n```tool\n{block.name} {json.dumps(block.input, ensure_ascii=True)}\n```")
        for block in message.content:
            if getattr(block, "type", "") == "tool_result":
                parts.append(f"\n```tool-result\n{block.content}\n```")
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path


# ---------------------------------------------------------------------------
# Project-scoped compatibility API
# ---------------------------------------------------------------------------


def get_project_session_dir(cwd: str | Path) -> Path:
    """Return the session directory for a project."""
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    session_dir = get_sessions_dir() / f"{path.name}-{digest}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session_snapshot(
    *,
    cwd: str | Path,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist a session snapshot. Saves both by ID and as latest."""
    return write_snapshot(
        get_project_session_dir(cwd),
        cwd=cwd,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        usage=usage,
        session_id=session_id,
        tool_metadata=tool_metadata,
    )


def load_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
    """Load the most recent session snapshot for the project."""
    return load_snapshot_file(get_project_session_dir(cwd) / "latest.json")


def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """List saved sessions for the project, newest first."""
    sessions = list_snapshots_in(
        get_project_session_dir(cwd),
        limit=limit,
        include_latest=True,
    )
    sessions.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return sessions[:limit]


def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """Load a specific session by ID."""
    return load_snapshot_by_id_in(get_project_session_dir(cwd), session_id)


def export_session_markdown(
    *,
    cwd: str | Path,
    messages: list[ConversationMessage],
) -> Path:
    """Export the session transcript as Markdown."""
    return export_transcript(
        get_project_session_dir(cwd),
        title="OpenHarness Session Transcript",
        messages=messages,
        include_tool_blocks=True,
    )

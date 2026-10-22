"""File-based permission request storage."""

from __future__ import annotations

import asyncio
import json
import os
import random
import string
import time
from pathlib import Path
from typing import Any

from openharness.swarm.lockfile import exclusive_file_lock
from openharness.swarm.mailbox import get_team_dir
from openharness.swarm.permission_models import (
    PermissionResolution,
    PermissionResponse,
    SwarmPermissionRequest,
    _get_agent_id,
    _get_agent_name,
    _get_team_name,
    _get_teammate_color,
)


def generate_request_id() -> str:
    """Generate a unique permission request ID.

    Format: ``perm-{timestamp_ms}-{random7}``, matching the TS implementation:
    ``perm-${Date.now()}-${Math.random().toString(36).substring(2, 9)}``
    """
    ts = int(time.time() * 1000)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
    return f"perm-{ts}-{rand}"


def generate_sandbox_request_id() -> str:
    """Generate a unique sandbox permission request ID.

    Format: ``sandbox-{timestamp_ms}-{random7}``.
    """
    ts = int(time.time() * 1000)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
    return f"sandbox-{ts}-{rand}"


# ---------------------------------------------------------------------------
# Permission directory helpers
# ---------------------------------------------------------------------------


def get_permission_dir(team_name: str) -> Path:
    """Return ~/.openharness/teams/{teamName}/permissions/"""
    return get_team_dir(team_name) / "permissions"


def _get_pending_dir(team_name: str) -> Path:
    return get_permission_dir(team_name) / "pending"


def _get_resolved_dir(team_name: str) -> Path:
    return get_permission_dir(team_name) / "resolved"


def _ensure_permission_dirs(team_name: str) -> None:
    for d in (
        get_permission_dir(team_name),
        _get_pending_dir(team_name),
        _get_resolved_dir(team_name),
    ):
        d.mkdir(parents=True, exist_ok=True)


def _pending_request_path(team_name: str, request_id: str) -> Path:
    return _get_pending_dir(team_name) / f"{request_id}.json"


def _resolved_request_path(team_name: str, request_id: str) -> Path:
    return _get_resolved_dir(team_name) / f"{request_id}.json"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_permission_request(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    description: str = "",
    permission_suggestions: list[Any] | None = None,
    team_name: str | None = None,
    worker_id: str | None = None,
    worker_name: str | None = None,
    worker_color: str | None = None,
) -> SwarmPermissionRequest:
    """Build a new :class:`SwarmPermissionRequest` with a generated ID.

    Missing worker/team fields are read from environment variables
    (``CLAUDE_CODE_AGENT_ID``, ``CLAUDE_CODE_AGENT_NAME``,
    ``CLAUDE_CODE_TEAM_NAME``, ``CLAUDE_CODE_AGENT_COLOR``).

    Args:
        tool_name: Name of the tool requesting permission.
        tool_use_id: Original tool-use ID from the execution context.
        tool_input: The tool's input parameters.
        description: Optional human-readable description of the operation.
        permission_suggestions: Optional list of suggested permission-rule dicts.
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).
        worker_id: Worker agent ID (falls back to ``CLAUDE_CODE_AGENT_ID``).
        worker_name: Worker agent name (falls back to ``CLAUDE_CODE_AGENT_NAME``).
        worker_color: Worker color (falls back to ``CLAUDE_CODE_AGENT_COLOR``).

    Returns:
        A new :class:`SwarmPermissionRequest` in *pending* state.

    Raises:
        ValueError: if team_name, worker_id, or worker_name cannot be resolved.
    """
    resolved_team = team_name or _get_team_name() or ""
    resolved_id = worker_id or _get_agent_id() or ""
    resolved_name = worker_name or _get_agent_name() or ""
    resolved_color = worker_color or _get_teammate_color()

    return SwarmPermissionRequest(
        id=generate_request_id(),
        worker_id=resolved_id,
        worker_name=resolved_name,
        worker_color=resolved_color,
        team_name=resolved_team,
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        description=description,
        input=tool_input,
        permission_suggestions=permission_suggestions or [],
        status="pending",
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# File-based storage: write / read / resolve / cleanup
# ---------------------------------------------------------------------------


def _sync_write_permission_request(
    request: SwarmPermissionRequest,
) -> SwarmPermissionRequest:
    _ensure_permission_dirs(request.team_name)
    pending_path = _pending_request_path(request.team_name, request.id)
    lock_path = _get_pending_dir(request.team_name) / ".lock"
    tmp_path = pending_path.with_suffix(".json.tmp")

    with exclusive_file_lock(lock_path):
        tmp_path.write_text(json.dumps(request.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp_path, pending_path)
    return request


async def write_permission_request(
    request: SwarmPermissionRequest,
) -> SwarmPermissionRequest:
    """Write *request* to the pending directory with file locking.

    Called by worker agents when they need permission approval from the leader.

    Args:
        request: The permission request to persist.

    Returns:
        The written request (same object, for convenience).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_write_permission_request, request)


async def read_pending_permissions(
    team_name: str | None = None,
) -> list[SwarmPermissionRequest]:
    """Read all pending permission requests for a team.

    Called by the team leader to see what requests need attention.  Requests
    are returned sorted oldest-first.

    Args:
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        List of pending :class:`SwarmPermissionRequest` objects.
    """
    team = team_name or _get_team_name()
    if not team:
        return []

    pending_dir = _get_pending_dir(team)
    if not pending_dir.exists():
        return []

    requests: list[SwarmPermissionRequest] = []
    for path in sorted(pending_dir.glob("*.json")):
        if path.name == ".lock":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            requests.append(SwarmPermissionRequest.from_dict(data))
        except (json.JSONDecodeError, KeyError):
            continue

    requests.sort(key=lambda r: r.created_at)
    return requests


async def read_resolved_permission(
    request_id: str,
    team_name: str | None = None,
) -> SwarmPermissionRequest | None:
    """Read a resolved permission request by ID.

    Called by workers to check if their request has been resolved.

    Args:
        request_id: The permission request ID to look up.
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        The resolved :class:`SwarmPermissionRequest`, or ``None`` if not yet
        resolved.
    """
    team = team_name or _get_team_name()
    if not team:
        return None

    resolved_path = _resolved_request_path(team, request_id)
    if not resolved_path.exists():
        return None

    try:
        data = json.loads(resolved_path.read_text(encoding="utf-8"))
        return SwarmPermissionRequest.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _sync_resolve_permission(
    request_id: str,
    resolution: PermissionResolution,
    team: str,
) -> bool:
    _ensure_permission_dirs(team)
    pending_path = _pending_request_path(team, request_id)
    resolved_path = _resolved_request_path(team, request_id)
    lock_path = _get_pending_dir(team) / ".lock"
    tmp_path = resolved_path.with_suffix(".json.tmp")

    with exclusive_file_lock(lock_path):
        if not pending_path.exists():
            return False

        try:
            data = json.loads(pending_path.read_text(encoding="utf-8"))
            request = SwarmPermissionRequest.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return False

        resolved_request = SwarmPermissionRequest(
            id=request.id,
            worker_id=request.worker_id,
            worker_name=request.worker_name,
            worker_color=request.worker_color,
            team_name=request.team_name,
            tool_name=request.tool_name,
            tool_use_id=request.tool_use_id,
            description=request.description,
            input=request.input,
            permission_suggestions=request.permission_suggestions,
            status="approved" if resolution.decision == "approved" else "rejected",
            resolved_by=resolution.resolved_by,
            resolved_at=time.time(),
            feedback=resolution.feedback,
            updated_input=resolution.updated_input,
            permission_updates=resolution.permission_updates,
            created_at=request.created_at,
        )

        tmp_path.write_text(
            json.dumps(resolved_request.to_dict(), indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, resolved_path)
        try:
            pending_path.unlink()
        except OSError:
            pass

    return True


async def resolve_permission(
    request_id: str,
    resolution: PermissionResolution,
    team_name: str | None = None,
) -> bool:
    """Resolve a permission request, moving it from pending/ to resolved/.

    Called by the team leader (or worker in self-resolution cases).

    Args:
        request_id: The permission request ID to resolve.
        resolution: The resolution data (decision, resolvedBy, etc.).
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        ``True`` if the request was found and resolved, ``False`` otherwise.
    """
    team = team_name or _get_team_name()
    if not team:
        return False
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _sync_resolve_permission, request_id, resolution, team
    )


def _sync_cleanup_old_resolutions(team: str, max_age_seconds: float) -> int:
    resolved_dir = _get_resolved_dir(team)
    if not resolved_dir.exists():
        return 0

    now = time.time()
    cleaned = 0

    for path in resolved_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            resolved_at = data.get("resolved_at") or data.get("created_at", 0)
            if now - resolved_at >= max_age_seconds:
                path.unlink()
                cleaned += 1
        except (json.JSONDecodeError, KeyError, OSError):
            try:
                path.unlink()
                cleaned += 1
            except OSError:
                pass

    return cleaned


async def cleanup_old_resolutions(
    team_name: str | None = None,
    max_age_seconds: float = 3600.0,
) -> int:
    """Clean up old resolved permission files.

    Called periodically to prevent file accumulation.

    Args:
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).
        max_age_seconds: Maximum age in seconds (default: 1 hour).

    Returns:
        Number of files removed.
    """
    team = team_name or _get_team_name()
    if not team:
        return 0
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _sync_cleanup_old_resolutions, team, max_age_seconds
    )


async def delete_resolved_permission(
    request_id: str,
    team_name: str | None = None,
) -> bool:
    """Delete a resolved permission file after a worker has processed it.

    Args:
        request_id: The permission request ID.
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        ``True`` if the file was found and deleted, ``False`` otherwise.
    """
    team = team_name or _get_team_name()
    if not team:
        return False

    resolved_path = _resolved_request_path(team, request_id)
    try:
        resolved_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Legacy / backward-compat helpers
# ---------------------------------------------------------------------------


async def poll_for_response(
    request_id: str,
    _agent_name: str | None = None,
    team_name: str | None = None,
) -> PermissionResponse | None:
    """Poll for a permission response (worker-side convenience function).

    Converts the resolved request into the simpler legacy response format.

    Args:
        request_id: The permission request ID to check.
        _agent_name: Unused; kept for API compatibility.
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        A :class:`PermissionResponse`, or ``None`` if not yet resolved.
    """
    from datetime import datetime, timezone

    resolved = await read_resolved_permission(request_id, team_name)
    if not resolved:
        return None

    ts = resolved.resolved_at or resolved.created_at
    return PermissionResponse(
        request_id=resolved.id,
        decision="approved" if resolved.status == "approved" else "denied",
        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z",
        feedback=resolved.feedback,
        updated_input=resolved.updated_input,
        permission_updates=resolved.permission_updates,
    )


async def remove_worker_response(
    request_id: str,
    _agent_name: str | None = None,
    team_name: str | None = None,
) -> None:
    """Remove a worker's response after processing (alias for delete_resolved_permission)."""
    await delete_resolved_permission(request_id, team_name)

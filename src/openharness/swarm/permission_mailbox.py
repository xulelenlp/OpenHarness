"""Mailbox-based permission request/response transport."""

from __future__ import annotations

import json
import time

from openharness.swarm.mailbox import (
    create_permission_request_message,
    create_permission_response_message,
    create_sandbox_permission_request_message,
    create_sandbox_permission_response_message,
    write_to_mailbox,
)
from openharness.swarm.permission_models import (
    PermissionResolution,
    SwarmPermissionRequest,
    _get_agent_id,
    _get_agent_name,
    _get_team_name,
    _get_teammate_color,
)


def is_team_leader(team_name: str | None = None) -> bool:
    """Return True if the current agent is a team leader.

    Team leaders don't have an agent ID set, or their ID is 'team-lead'.
    """
    team = team_name or _get_team_name()
    if not team:
        return False
    agent_id = _get_agent_id()
    return not agent_id or agent_id == "team-lead"


def is_swarm_worker() -> bool:
    """Return True if the current agent is a worker in a swarm."""
    team_name = _get_team_name()
    agent_id = _get_agent_id()
    return bool(team_name) and bool(agent_id) and not is_team_leader()


# ---------------------------------------------------------------------------
# Leader name lookup
# ---------------------------------------------------------------------------


async def get_leader_name(team_name: str | None = None) -> str | None:
    """Get the leader's agent name from the team file.

    This is needed to address permission requests to the leader's mailbox.

    Args:
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        The leader's name string, or ``None`` if the team file is missing.
        Falls back to ``'team-lead'`` if the lead member is not found.
    """
    from openharness.swarm.team_lifecycle import read_team_file_async

    team = team_name or _get_team_name()
    if not team:
        return None

    team_file = await read_team_file_async(team)
    if not team_file:
        return None

    lead_id = team_file.lead_agent_id
    if lead_id and lead_id in team_file.members:
        return team_file.members[lead_id].name

    return "team-lead"


# ---------------------------------------------------------------------------
# Mailbox-based permission send/receive
# ---------------------------------------------------------------------------


async def send_permission_request_via_mailbox(
    request: SwarmPermissionRequest,
) -> bool:
    """Send a permission request to the leader via the mailbox system.

    This is the mailbox-based approach for forwarding permission requests.
    Writes a ``permission_request`` message to the leader's mailbox.

    Args:
        request: The permission request to send.

    Returns:
        ``True`` if the message was sent successfully.
    """
    leader_name = await get_leader_name(request.team_name)
    if not leader_name:
        return False

    try:
        msg = create_permission_request_message(
            sender=request.worker_name,
            recipient=leader_name,
            request_data={
                "request_id": request.id,
                "agent_id": request.worker_name,
                "tool_name": request.tool_name,
                "tool_use_id": request.tool_use_id,
                "description": request.description,
                "input": request.input,
                "permission_suggestions": request.permission_suggestions,
            },
        )

        await write_to_mailbox(
            leader_name,
            {
                "from": request.worker_name,
                "text": json.dumps(msg.payload),
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()
                ),
                "color": request.worker_color,
            },
            request.team_name,
        )
        return True
    except OSError:
        return False


async def send_permission_response_via_mailbox(
    worker_name: str,
    resolution: PermissionResolution,
    request_id: str,
    team_name: str | None = None,
) -> bool:
    """Send a permission response to a worker via the mailbox system.

    Called by the leader when approving/denying a permission request.

    Args:
        worker_name: The worker's name to send the response to.
        resolution: The permission resolution.
        request_id: The original request ID.
        team_name: Team name (falls back to ``CLAUDE_CODE_TEAM_NAME``).

    Returns:
        ``True`` if the message was sent successfully.
    """
    team = team_name or _get_team_name()
    if not team:
        return False

    sender_name = _get_agent_name() or "team-lead"
    subtype = "success" if resolution.decision == "approved" else "error"

    try:
        msg = create_permission_response_message(
            sender=sender_name,
            recipient=worker_name,
            response_data={
                "request_id": request_id,
                "subtype": subtype,
                "error": resolution.feedback if subtype == "error" else None,
                "updated_input": resolution.updated_input,
                "permission_updates": resolution.permission_updates,
            },
        )

        await write_to_mailbox(
            worker_name,
            {
                "from": sender_name,
                "text": json.dumps(msg.payload),
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()
                ),
            },
            team,
        )
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Sandbox permission mailbox helpers
# ---------------------------------------------------------------------------


async def send_sandbox_permission_request_via_mailbox(
    host: str,
    request_id: str,
    team_name: str | None = None,
) -> bool:
    """Send a sandbox permission request to the leader via the mailbox system.

    Called by workers when sandbox runtime needs network access approval.

    Args:
        host: The host requesting network access.
        request_id: Unique ID for this request.
        team_name: Optional team name.

    Returns:
        ``True`` if the message was sent successfully.
    """
    team = team_name or _get_team_name()
    if not team:
        return False

    leader_name = await get_leader_name(team)
    if not leader_name:
        return False

    worker_id = _get_agent_id()
    worker_name = _get_agent_name()
    worker_color = _get_teammate_color()

    if not worker_id or not worker_name:
        return False

    try:
        msg = create_sandbox_permission_request_message(
            sender=worker_name,
            recipient=leader_name,
            request_data={
                "requestId": request_id,
                "workerId": worker_id,
                "workerName": worker_name,
                "workerColor": worker_color,
                "host": host,
            },
        )

        await write_to_mailbox(
            leader_name,
            {
                "from": worker_name,
                "text": json.dumps(msg.payload),
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()
                ),
                "color": worker_color,
            },
            team,
        )
        return True
    except OSError:
        return False


async def send_sandbox_permission_response_via_mailbox(
    worker_name: str,
    request_id: str,
    host: str,
    allow: bool,
    team_name: str | None = None,
) -> bool:
    """Send a sandbox permission response to a worker via the mailbox system.

    Called by the leader when approving/denying a sandbox network access request.

    Args:
        worker_name: The worker's name to send the response to.
        request_id: The original request ID.
        host: The host that was approved/denied.
        allow: Whether the connection is allowed.
        team_name: Optional team name.

    Returns:
        ``True`` if the message was sent successfully.
    """
    team = team_name or _get_team_name()
    if not team:
        return False

    sender_name = _get_agent_name() or "team-lead"

    try:
        msg = create_sandbox_permission_response_message(
            sender=sender_name,
            recipient=worker_name,
            response_data={
                "requestId": request_id,
                "host": host,
                "allow": allow,
            },
        )

        await write_to_mailbox(
            worker_name,
            {
                "from": sender_name,
                "text": json.dumps(msg.payload),
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()
                ),
            },
            team,
        )
        return True
    except OSError:
        return False

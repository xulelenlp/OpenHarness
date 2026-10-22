"""Permission sync protocol for leader-worker coordination in OpenHarness swarms."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from openharness.swarm.mailbox import (
    MailboxMessage,
    TeammateMailbox,
)
from openharness.swarm.permission_models import (
    SwarmPermissionRequest,
    SwarmPermissionResponse,
    _is_read_only,
)

if TYPE_CHECKING:
    from openharness.permissions.checker import PermissionChecker


async def send_permission_request(
    request: SwarmPermissionRequest,
    team_name: str,
    worker_id: str,
    leader_id: str = "leader",
) -> None:
    """Serialize *request* and write it to the leader's mailbox.

    This is the original structured-payload approach.  For new code prefer
    :func:`send_permission_request_via_mailbox`.

    Args:
        request: The permission request to forward.
        team_name: The swarm team name used for mailbox routing.
        worker_id: The sending worker's agent ID.
        leader_id: The leader's agent ID (default ``"leader"``).
    """
    payload: dict[str, Any] = {
        "request_id": request.id,
        "tool_name": request.tool_name,
        "tool_use_id": request.tool_use_id,
        "input": request.input,
        "description": request.description,
        "permission_suggestions": request.permission_suggestions,
        "worker_id": worker_id,
    }
    msg = MailboxMessage(
        id=str(uuid.uuid4()),
        type="permission_request",
        sender=worker_id,
        recipient=leader_id,
        payload=payload,
        timestamp=time.time(),
    )
    leader_mailbox = TeammateMailbox(team_name, leader_id)
    await leader_mailbox.write(msg)


async def poll_permission_response(
    team_name: str,
    worker_id: str,
    request_id: str,
    timeout: float = 60.0,
) -> SwarmPermissionResponse | None:
    """Poll the worker's own mailbox until a matching ``permission_response`` arrives.

    Checks every 0.5 s up to *timeout* seconds.  When a response matching
    *request_id* is found, the message is marked read and the decoded
    :class:`SwarmPermissionResponse` is returned.

    Args:
        team_name: The swarm team name.
        worker_id: The worker's agent ID (owns this mailbox).
        request_id: The ``SwarmPermissionRequest.id`` to match against.
        timeout: Maximum seconds to wait before returning ``None``.

    Returns:
        A :class:`SwarmPermissionResponse`, or ``None`` on timeout.
    """
    worker_mailbox = TeammateMailbox(team_name, worker_id)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        messages = await worker_mailbox.read_all(unread_only=True)
        for msg in messages:
            if msg.type == "permission_response":
                payload = msg.payload
                if payload.get("request_id") == request_id:
                    await worker_mailbox.mark_read(msg.id)
                    return SwarmPermissionResponse(
                        request_id=payload["request_id"],
                        allowed=bool(payload.get("allowed", False)),
                        feedback=payload.get("feedback"),
                        updated_rules=payload.get("updated_rules", []),
                    )
        await asyncio.sleep(0.5)

    return None


# ---------------------------------------------------------------------------
# Leader helper: evaluate and send response
# ---------------------------------------------------------------------------


async def handle_permission_request(
    request: SwarmPermissionRequest,
    checker: "PermissionChecker",
) -> SwarmPermissionResponse:
    """Evaluate *request* using the existing :class:`PermissionChecker`.

    Read-only tools are auto-approved without consulting the checker.  For
    all other tools the checker's ``evaluate`` method is called; if the tool
    is allowed or only requires confirmation (and nothing blocks it), it is
    approved; otherwise it is denied.

    Args:
        request: The incoming permission request from a worker.
        checker: An already-configured :class:`~openharness.permissions.checker.PermissionChecker`.

    Returns:
        A :class:`SwarmPermissionResponse` with the decision.
    """
    if _is_read_only(request.tool_name):
        return SwarmPermissionResponse(
            request_id=request.id,
            allowed=True,
            feedback=None,
        )

    file_path: str | None = (
        request.input.get("file_path")  # type: ignore[assignment]
        or request.input.get("path")
        or None
    )
    command: str | None = request.input.get("command")  # type: ignore[assignment]

    decision = checker.evaluate(
        request.tool_name,
        is_read_only=False,
        file_path=file_path,
        command=command,
    )

    allowed = decision.allowed
    feedback: str | None = None if allowed else decision.reason

    return SwarmPermissionResponse(
        request_id=request.id,
        allowed=allowed,
        feedback=feedback,
    )


# ---------------------------------------------------------------------------
# Leader helper: write response back to a worker's mailbox
# ---------------------------------------------------------------------------


async def send_permission_response(
    response: SwarmPermissionResponse,
    team_name: str,
    worker_id: str,
    leader_id: str = "leader",
) -> None:
    """Write *response* to the worker's mailbox.

    This is the original structured-payload approach.  For new code prefer
    :func:`send_permission_response_via_mailbox`.

    Args:
        response: The resolution to send.
        team_name: The swarm team name.
        worker_id: The target worker's agent ID.
        leader_id: The sending leader's agent ID (default ``"leader"``).
    """
    payload: dict[str, Any] = {
        "request_id": response.request_id,
        "allowed": response.allowed,
        "feedback": response.feedback,
        "updated_rules": response.updated_rules,
    }
    msg = MailboxMessage(
        id=str(uuid.uuid4()),
        type="permission_response",
        sender=leader_id,
        recipient=worker_id,
        payload=payload,
        timestamp=time.time(),
    )
    worker_mailbox = TeammateMailbox(team_name, worker_id)
    await worker_mailbox.write(msg)

# Backwards-compatible re-exports (the implementation moved to sibling modules).
from openharness.swarm.permission_mailbox import (  # noqa: E402,F401
    is_swarm_worker,
    is_team_leader,
    send_permission_request_via_mailbox,
    send_permission_response_via_mailbox,
    send_sandbox_permission_request_via_mailbox,
    send_sandbox_permission_response_via_mailbox,
)
from openharness.swarm.permission_models import (  # noqa: E402,F401
    PermissionResolution,
    PermissionResponse,
    _get_agent_id,
    _get_agent_name,
    _get_team_name,
    _get_teammate_color,
)
from openharness.swarm.permission_storage import (  # noqa: E402,F401
    cleanup_old_resolutions,
    create_permission_request,
    delete_resolved_permission,
    generate_request_id,
    generate_sandbox_request_id,
    get_permission_dir,
    poll_for_response,
    read_pending_permissions,
    read_resolved_permission,
    remove_worker_response,
    resolve_permission,
    write_permission_request,
)

"""Permission request/response data models and environment helpers."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass

def _get_team_name() -> str | None:
    return os.environ.get("CLAUDE_CODE_TEAM_NAME")


def _get_agent_id() -> str | None:
    return os.environ.get("CLAUDE_CODE_AGENT_ID")


def _get_agent_name() -> str | None:
    return os.environ.get("CLAUDE_CODE_AGENT_NAME")


def _get_teammate_color() -> str | None:
    return os.environ.get("CLAUDE_CODE_AGENT_COLOR")


# ---------------------------------------------------------------------------
# Read-only tool heuristic
# ---------------------------------------------------------------------------

_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "glob",
        "grep",
        "web_fetch",
        "web_search",
        "task_get",
        "task_list",
        "task_output",
        "cron_list",
    }
)


def _is_read_only(tool_name: str) -> bool:
    """Return True for tools that are considered safe/read-only."""
    return tool_name in _READ_ONLY_TOOLS


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SwarmPermissionRequest:
    """Permission request forwarded from a worker to the team leader.

    All fields are present to match the TS SwarmPermissionRequestSchema.
    """

    id: str
    """Unique identifier for this request."""

    worker_id: str
    """The requesting worker's agent ID (CLAUDE_CODE_AGENT_ID)."""

    worker_name: str
    """The requesting worker's agent name (CLAUDE_CODE_AGENT_NAME)."""

    team_name: str
    """Team name for routing."""

    tool_name: str
    """Name of the tool requiring permission (e.g. 'Bash', 'Edit')."""

    tool_use_id: str
    """Original tool-use ID from the worker's execution context."""

    description: str
    """Human-readable description of the requested operation."""

    input: dict[str, Any]
    """Serialized tool input parameters."""

    # Optional / defaulted fields
    permission_suggestions: list[Any] = field(default_factory=list)
    """Suggested rule updates produced by the worker's local permission system."""

    worker_color: str | None = None
    """The requesting worker's assigned color (CLAUDE_CODE_AGENT_COLOR)."""

    status: Literal["pending", "approved", "rejected"] = "pending"
    """Current status of the request."""

    resolved_by: Literal["worker", "leader"] | None = None
    """Who resolved the request."""

    resolved_at: float | None = None
    """Timestamp (seconds since epoch) when the request was resolved."""

    feedback: str | None = None
    """Optional rejection reason or leader comment."""

    updated_input: dict[str, Any] | None = None
    """Modified input if changed by the resolver."""

    permission_updates: list[Any] | None = None
    """'Always allow' rules applied during resolution."""

    created_at: float = field(default_factory=time.time)
    """Timestamp when request was created."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "team_name": self.team_name,
            "tool_name": self.tool_name,
            "tool_use_id": self.tool_use_id,
            "description": self.description,
            "input": self.input,
            "permission_suggestions": self.permission_suggestions,
            "worker_color": self.worker_color,
            "status": self.status,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "feedback": self.feedback,
            "updated_input": self.updated_input,
            "permission_updates": self.permission_updates,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmPermissionRequest":
        return cls(
            id=data["id"],
            worker_id=data.get("worker_id", data.get("workerId", "")),
            worker_name=data.get("worker_name", data.get("workerName", "")),
            team_name=data.get("team_name", data.get("teamName", "")),
            tool_name=data.get("tool_name", data.get("toolName", "")),
            tool_use_id=data.get("tool_use_id", data.get("toolUseId", "")),
            description=data.get("description", ""),
            input=data.get("input", {}),
            permission_suggestions=data.get(
                "permission_suggestions",
                data.get("permissionSuggestions", []),
            ),
            worker_color=data.get("worker_color", data.get("workerColor")),
            status=data.get("status", "pending"),
            resolved_by=data.get("resolved_by", data.get("resolvedBy")),
            resolved_at=data.get("resolved_at", data.get("resolvedAt")),
            feedback=data.get("feedback"),
            updated_input=data.get("updated_input", data.get("updatedInput")),
            permission_updates=data.get(
                "permission_updates", data.get("permissionUpdates")
            ),
            created_at=data.get("created_at", data.get("createdAt", time.time())),
        )


@dataclass
class PermissionResolution:
    """Resolution data returned when leader/worker resolves a request."""

    decision: Literal["approved", "rejected"]
    """Decision: approved or rejected."""

    resolved_by: Literal["worker", "leader"]
    """Who resolved the request."""

    feedback: str | None = None
    """Optional feedback message if rejected."""

    updated_input: dict[str, Any] | None = None
    """Optional updated input if the resolver modified it."""

    permission_updates: list[Any] | None = None
    """Permission updates to apply (e.g. 'always allow' rules)."""


@dataclass
class PermissionResponse:
    """Legacy response type for worker polling (backward compatibility)."""

    request_id: str
    """ID of the request this responds to."""

    decision: Literal["approved", "denied"]
    """Decision: approved or denied."""

    timestamp: str
    """ISO timestamp when response was created."""

    feedback: str | None = None
    """Optional feedback message if denied."""

    updated_input: dict[str, Any] | None = None
    """Optional updated input if the resolver modified it."""

    permission_updates: list[Any] | None = None
    """Permission updates to apply."""


@dataclass
class SwarmPermissionResponse:
    """Response sent from the leader back to the requesting worker."""

    request_id: str
    """ID of the ``SwarmPermissionRequest`` this responds to."""

    allowed: bool
    """True if the tool use is approved."""

    feedback: str | None = None
    """Optional rejection reason or leader comment."""

    updated_rules: list[dict[str, Any]] = field(default_factory=list)
    """Permission-rule updates the leader decided to apply."""

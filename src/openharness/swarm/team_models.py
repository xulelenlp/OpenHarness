"""Persistent team data models."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openharness.swarm.types import BackendType


def sanitize_name(name: str) -> str:
    """Replace all non-alphanumeric characters with hyphens and lowercase.

    Mirrors TS ``sanitizeName``:
    ``name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase()``
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", name).lower()


def sanitize_agent_name(name: str) -> str:
    """Replace ``@`` with ``-`` to avoid ambiguity in agentName@teamName format.

    Mirrors TS ``sanitizeAgentName``:
    ``name.replace(/@/g, '-')``
    """
    return name.replace("@", "-")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AllowedPath:
    """A path that all team members can edit without asking for permission."""

    path: str
    """Absolute directory path."""

    tool_name: str
    """The tool this applies to (e.g. 'Edit', 'Write')."""

    added_by: str
    """Agent name who added this rule."""

    added_at: float = field(default_factory=time.time)
    """Timestamp when the rule was added."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "tool_name": self.tool_name,
            "added_by": self.added_by,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AllowedPath":
        return cls(
            path=data["path"],
            tool_name=data.get("tool_name", data.get("toolName", "")),
            added_by=data.get("added_by", data.get("addedBy", "")),
            added_at=data.get("added_at", data.get("addedAt", time.time())),
        )


@dataclass
class TeamMember:
    """A member of a swarm team."""

    agent_id: str
    name: str
    backend_type: BackendType
    joined_at: float

    # Optional fields matching TS TeamFile member shape
    agent_type: str | None = None
    """Type/role of the agent (e.g. 'researcher', 'test-runner')."""

    model: str | None = None
    """Model identifier used by this agent."""

    prompt: str | None = None
    """Initial system prompt for this agent."""

    color: str | None = None
    """Assigned display colour (e.g. 'red', 'blue', 'green')."""

    plan_mode_required: bool = False
    """Whether this agent requires plan-mode approval before acting."""

    session_id: str | None = None
    """Actual session UUID of this agent (for discovery)."""

    subscriptions: list[str] = field(default_factory=list)
    """Event topics this agent subscribes to."""

    is_active: bool = True
    """False when idle; undefined/True when active."""

    mode: str | None = None
    """Current permission mode for this agent (e.g. 'auto', 'manual')."""

    tmux_pane_id: str = ""
    """Tmux/iTerm2 pane ID for pane-backed agents."""

    cwd: str = ""
    """Working directory for this agent."""

    worktree_path: str | None = None
    """Git worktree path, if the agent operates in an isolated worktree."""

    permissions: list[str] = field(default_factory=list)
    """Legacy permission strings list."""

    status: Literal["active", "idle", "stopped"] = "active"
    """Coarse status of this agent."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "backend_type": self.backend_type,
            "joined_at": self.joined_at,
            "agent_type": self.agent_type,
            "model": self.model,
            "prompt": self.prompt,
            "color": self.color,
            "plan_mode_required": self.plan_mode_required,
            "session_id": self.session_id,
            "subscriptions": self.subscriptions,
            "is_active": self.is_active,
            "mode": self.mode,
            "tmux_pane_id": self.tmux_pane_id,
            "cwd": self.cwd,
            "worktree_path": self.worktree_path,
            "permissions": self.permissions,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamMember":
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            backend_type=data["backend_type"],
            joined_at=data["joined_at"],
            agent_type=data.get("agent_type"),
            model=data.get("model"),
            prompt=data.get("prompt"),
            color=data.get("color"),
            plan_mode_required=data.get("plan_mode_required", False),
            session_id=data.get("session_id"),
            subscriptions=data.get("subscriptions", []),
            is_active=data.get("is_active", True),
            mode=data.get("mode"),
            tmux_pane_id=data.get("tmux_pane_id", ""),
            cwd=data.get("cwd", ""),
            worktree_path=data.get("worktree_path"),
            permissions=data.get("permissions", []),
            status=data.get("status", "active"),
        )


@dataclass
class TeamFile:
    """Persistent team metadata stored as team.json inside the team directory."""

    name: str
    created_at: float

    description: str = ""

    lead_agent_id: str = ""
    """Agent ID of the team leader."""

    lead_session_id: str | None = None
    """Actual session UUID of the leader (for discovery)."""

    hidden_pane_ids: list[str] = field(default_factory=list)
    """Pane IDs that are currently hidden from the UI."""

    members: dict[str, TeamMember] = field(default_factory=dict)
    """Dict mapping agent_id → TeamMember."""

    team_allowed_paths: list[AllowedPath] = field(default_factory=list)
    """Paths all teammates can edit without asking."""

    allowed_paths: list[str] = field(default_factory=list)
    """Legacy list of allowed path strings."""

    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "lead_agent_id": self.lead_agent_id,
            "lead_session_id": self.lead_session_id,
            "hidden_pane_ids": self.hidden_pane_ids,
            "members": {k: v.to_dict() for k, v in self.members.items()},
            "team_allowed_paths": [p.to_dict() for p in self.team_allowed_paths],
            "allowed_paths": self.allowed_paths,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamFile":
        members = {
            k: TeamMember.from_dict(v)
            for k, v in data.get("members", {}).items()
        }
        team_allowed_paths = [
            AllowedPath.from_dict(p)
            for p in data.get("team_allowed_paths", [])
        ]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            created_at=data["created_at"],
            lead_agent_id=data.get("lead_agent_id", ""),
            lead_session_id=data.get("lead_session_id"),
            hidden_pane_ids=data.get("hidden_pane_ids", []),
            members=members,
            team_allowed_paths=team_allowed_paths,
            allowed_paths=data.get("allowed_paths", []),
            metadata=data.get("metadata", {}),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Atomically write this team file to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.rename(path)

    @classmethod
    def load(cls, path: Path) -> "TeamFile":
        """Load a TeamFile from *path*.

        Raises:
            FileNotFoundError: if *path* does not exist.
            json.JSONDecodeError: if the file is not valid JSON.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

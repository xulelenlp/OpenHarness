"""Workspace creation, seeding, and health checks."""

from __future__ import annotations

import json
from pathlib import Path

from ohmo.workspace.paths import (
    get_attachments_dir,
    get_bootstrap_path,
    get_gateway_config_path,
    get_groups_dir,
    get_identity_path,
    get_logs_dir,
    get_memory_dir,
    get_memory_index_path,
    get_plugins_dir,
    get_sessions_dir,
    get_skills_dir,
    get_soul_path,
    get_state_path,
    get_user_path,
    get_workspace_root,
)
from ohmo.workspace.templates import (
    BOOTSTRAP_TEMPLATE,
    IDENTITY_TEMPLATE,
    MEMORY_INDEX_TEMPLATE,
    SOUL_TEMPLATE,
    USER_TEMPLATE,
)


def ensure_workspace(workspace: str | Path | None = None) -> Path:
    """Create the workspace if needed and return its root."""
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    get_memory_dir(root).mkdir(parents=True, exist_ok=True)
    get_skills_dir(root).mkdir(parents=True, exist_ok=True)
    get_plugins_dir(root).mkdir(parents=True, exist_ok=True)
    get_groups_dir(root).mkdir(parents=True, exist_ok=True)
    get_sessions_dir(root).mkdir(parents=True, exist_ok=True)
    get_logs_dir(root).mkdir(parents=True, exist_ok=True)
    get_attachments_dir(root).mkdir(parents=True, exist_ok=True)
    return root


def initialize_workspace(workspace: str | Path | None = None) -> Path:
    """Create the workspace and seed template files when missing."""
    root = ensure_workspace(workspace)
    templates = {
        get_soul_path(root): SOUL_TEMPLATE,
        get_user_path(root): USER_TEMPLATE,
        get_memory_index_path(root): MEMORY_INDEX_TEMPLATE,
        get_identity_path(root): IDENTITY_TEMPLATE,
    }
    for path, content in templates.items():
        if not path.exists():
            path.write_text(content.strip() + "\n", encoding="utf-8")
    state_path = get_state_path(root)
    state_data = {"app": "ohmo", "workspace": str(root.resolve())}
    if not state_path.exists():
        state_path.write_text(json.dumps(state_data, indent=2) + "\n", encoding="utf-8")
    else:
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state_data = {"app": "ohmo", "workspace": str(root.resolve())}
    bootstrap_path = get_bootstrap_path(root)
    if not state_data.get("bootstrap_seeded"):
        state_data["bootstrap_seeded"] = True
        if not bootstrap_path.exists():
            bootstrap_path.write_text(BOOTSTRAP_TEMPLATE.strip() + "\n", encoding="utf-8")
        state_path.write_text(json.dumps(state_data, indent=2) + "\n", encoding="utf-8")
    gateway_path = get_gateway_config_path(root)
    if not gateway_path.exists():
        gateway_path.write_text(
            json.dumps(
                {
                    "provider_profile": "codex",
                    "enabled_channels": [],
                    "session_routing": "chat-thread",
                    "send_progress": True,
                    "send_tool_hints": True,
                    "permission_mode": "default",
                    "sandbox_enabled": False,
                    "allow_remote_admin_commands": False,
                    "allowed_remote_admin_commands": [],
                    "log_level": "INFO",
                    "channel_configs": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return root


def workspace_health(workspace: str | Path | None = None) -> dict[str, bool]:
    """Return presence checks for the key workspace assets."""
    root = get_workspace_root(workspace)
    return {
        "workspace": root.exists(),
        "soul": get_soul_path(root).exists(),
        "user": get_user_path(root).exists(),
        "identity": get_identity_path(root).exists(),
        "memory_dir": get_memory_dir(root).exists(),
        "skills_dir": get_skills_dir(root).exists(),
        "plugins_dir": get_plugins_dir(root).exists(),
        "groups_dir": get_groups_dir(root).exists(),
        "memory_index": get_memory_index_path(root).exists(),
        "sessions_dir": get_sessions_dir(root).exists(),
        "gateway_config": get_gateway_config_path(root).exists(),
    }

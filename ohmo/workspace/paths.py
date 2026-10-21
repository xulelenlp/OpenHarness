"""Path resolution for the ``.ohmo`` workspace."""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_DIRNAME = ".ohmo"


def get_workspace_root(workspace: str | Path | None = None) -> Path:
    """Return the ohmo workspace root.

    Resolution order:
    1. Explicit ``workspace`` argument
    2. ``OHMO_WORKSPACE`` environment variable
    3. ``~/.ohmo``
    """
    explicit = workspace or os.environ.get("OHMO_WORKSPACE")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.name == WORKSPACE_DIRNAME else path
    return (Path.home() / WORKSPACE_DIRNAME).resolve()


def get_soul_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "soul.md"


def get_user_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "user.md"


def get_identity_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "identity.md"


def get_bootstrap_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "BOOTSTRAP.md"


def get_memory_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "memory"


def get_skills_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "skills"


def get_plugins_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "plugins"


def get_groups_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "groups"


def get_memory_index_path(workspace: str | Path | None = None) -> Path:
    return get_memory_dir(workspace) / "MEMORY.md"


def get_sessions_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "sessions"


def get_logs_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "logs"


def get_attachments_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "attachments"


def get_state_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "state.json"


def get_gateway_config_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "gateway.json"


def get_gateway_restart_notice_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "gateway-restart-notice.json"



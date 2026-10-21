"""Filesystem layout, templates, and lifecycle for the ``.ohmo`` workspace."""

from __future__ import annotations

from ohmo.workspace.lifecycle import (
    ensure_workspace,
    initialize_workspace,
    workspace_health,
)
from ohmo.workspace.paths import (
    WORKSPACE_DIRNAME,
    get_attachments_dir,
    get_bootstrap_path,
    get_gateway_config_path,
    get_gateway_restart_notice_path,
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

__all__ = [
    "BOOTSTRAP_TEMPLATE",
    "IDENTITY_TEMPLATE",
    "MEMORY_INDEX_TEMPLATE",
    "SOUL_TEMPLATE",
    "USER_TEMPLATE",
    "WORKSPACE_DIRNAME",
    "ensure_workspace",
    "get_attachments_dir",
    "get_bootstrap_path",
    "get_gateway_config_path",
    "get_gateway_restart_notice_path",
    "get_groups_dir",
    "get_identity_path",
    "get_logs_dir",
    "get_memory_dir",
    "get_memory_index_path",
    "get_plugins_dir",
    "get_sessions_dir",
    "get_skills_dir",
    "get_soul_path",
    "get_state_path",
    "get_user_path",
    "get_workspace_root",
    "initialize_workspace",
    "workspace_health",
]

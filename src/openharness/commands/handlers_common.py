# ruff: noqa: F401
"""Shared imports for built-in slash command handler modules."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable, Literal, get_args

import pyperclip

from openharness.api.provider import auth_status, detect_provider
from openharness.auth.manager import AuthManager
from openharness.autopilot import RepoAutopilotStore
from openharness.bridge import get_bridge_manager
from openharness.bridge.types import WorkSecret
from openharness.bridge.work_secret import build_sdk_url, decode_work_secret, encode_work_secret
from openharness.config.paths import (
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_feedback_log_path,
    get_project_config_dir,
    get_project_issue_file,
    get_project_pr_comments_file,
)
from openharness.config.settings import (
    Settings,
    display_model_setting,
    load_settings,
    save_settings,
)
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.memory import (
    add_memory_entry,
    get_memory_entrypoint,
    get_project_memory_dir,
    list_memory_files,
    migrate_memory,
    remove_memory_entry,
    scan_memory_files,
)
from openharness.memory.agent import (
    ensure_agent_memory_vault,
    get_agent_memory_entrypoint,
    initialize_agent_memory_from_snapshot,
)
from openharness.memory.schema import (
    DEFAULT_MEMORY_SCOPE,
    DEFAULT_MEMORY_TYPE,
    MEMORY_TYPES,
    is_disabled_metadata,
    is_memory_expired,
    parse_memory_scope,
    parse_memory_type,
    split_memory_file,
)
from openharness.memory.team import (
    check_team_memory_secrets,
    ensure_team_memory_vault,
    get_team_memory_dir,
)
from openharness.output_styles import load_output_styles
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.plugins import load_plugins
from openharness.plugins.installer import install_plugin_from_path, uninstall_plugin
from openharness.plugins.types import PluginCommandDefinition
from openharness.prompts import build_runtime_system_prompt
from openharness.services import (
    build_post_compact_messages,
    compact_conversation,
    compact_messages,
    estimate_conversation_tokens,
    summarize_messages,
)
from openharness.services.autodream import (
    diff_memory_dirs,
    format_memory_diff,
    latest_memory_backup,
    read_last_consolidated_at,
    restore_memory_backup,
    start_dream_now,
)
from openharness.services.memory_extract import extract_memories_from_turn
from openharness.services.session_backend import DEFAULT_SESSION_BACKEND, SessionBackend
from openharness.services.session_memory import (
    get_session_memory_content,
    get_session_memory_path,
    update_session_memory_file,
)
from openharness.skills import load_skill_registry
from openharness.skills.types import SkillDefinition
from openharness.tasks import get_task_manager

if TYPE_CHECKING:
    from openharness.config.settings import ProviderProfile
    from openharness.state import AppStateStore
    from openharness.tools.base import ToolRegistry



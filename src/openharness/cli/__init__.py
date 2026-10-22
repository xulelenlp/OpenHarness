"""CLI entry point using typer.

The CLI implementation is split across focused modules for maintainability, but
``openharness.cli`` remains the stable import surface:

* ``from openharness.cli import app`` keeps working (and is the ``abc`` script
  entry point declared in ``pyproject.toml``).
* Legacy private helpers that tests and external callers patch, most notably
  ``_build_dry_run_preview``, are re-exported here. ``main`` resolves the
  preview helpers through this package module at call time so monkeypatching
  ``openharness.cli._build_dry_run_preview`` still takes effect.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import typer

from openharness.cli.app import (
    _version_callback,
    app,
    auth_app,
    autopilot_app,
    config_app,
    cron_app,
    mcp_app,
    plugin_app,
    provider_app,
)
from openharness.cli.commands.auth import (
    _AUTH_SOURCE_LABELS,
    _PROVIDER_LABELS,
    _bind_external_provider,
    _login_provider,
    _maybe_update_default_model_for_provider,
    _run_copilot_login,
    auth_claude_login,
    auth_codex_login,
    auth_copilot_login,
    auth_copilot_logout,
    auth_login,
    auth_logout,
    auth_status_cmd,
    auth_switch,
)
from openharness.cli.commands.autopilot import (
    autopilot_add_cmd,
    autopilot_context_cmd,
    autopilot_export_dashboard_cmd,
    autopilot_install_cron_cmd,
    autopilot_journal_cmd,
    autopilot_list_cmd,
    autopilot_run_next_cmd,
    autopilot_scan_cmd,
    autopilot_status_cmd,
    autopilot_tick_cmd,
)
from openharness.cli.commands.config import (
    _config_coerce_value,
    _config_resolve_target,
    config_set,
    config_show,
)
from openharness.cli.commands.cron import (
    cron_history_cmd,
    cron_list_cmd,
    cron_logs_cmd,
    cron_start,
    cron_status_cmd,
    cron_stop,
    cron_toggle_cmd,
)
from openharness.cli.commands.mcp import mcp_add, mcp_list, mcp_remove
from openharness.cli.commands.plugin import (
    plugin_install,
    plugin_list,
    plugin_uninstall,
)
from openharness.cli.commands.provider import (
    provider_add,
    provider_edit,
    provider_list,
    provider_remove,
    provider_use,
)
from openharness.cli.commands.setup import (
    _configure_custom_profile_via_setup,
    _default_credential_slot_for_profile,
    _ensure_preset_profile,
    _ensure_profile_auth,
    _format_profile_choice_label,
    _maybe_update_profile_auth,
    _prompt_api_key_for_profile,
    _prompt_model_for_profile,
    _select_setup_workflow,
    _specialize_setup_target,
    _styled_missing_suffix,
    setup_cmd,
)
from openharness.cli.main import main
from openharness.cli.preview import (
    _PREVIEW_STOPWORDS,
    _build_dry_run_preview,
    _candidate_entry,
    _dry_run_command_behavior,
    _evaluate_dry_run_readiness,
    _format_dry_run_preview,
    _mcp_transport_preview,
    _recommend_preview_candidates,
    _safe_short,
    _schema_argument_preview,
    _score_candidate_match,
    _tokenize_preview_text,
    _validate_mcp_server,
)
from openharness.ui.prompts import (
    can_use_questionary,
    confirm_prompt,
    secret_prompt,
    select_from_menu,
    select_with_questionary,
    text_prompt,
)
from openharness.version import __version__

# Backwards-compatible private names (tests and older callers use these).
_can_use_questionary = can_use_questionary
_select_with_questionary = select_with_questionary
_text_prompt = text_prompt
_secret_prompt = secret_prompt
_confirm_prompt = confirm_prompt
_select_from_menu = select_from_menu

__all__ = [
    # stdlib and third-party modules that used to live in this module namespace
    "Optional",
    "Path",
    "StringIO",
    "json",
    "os",
    "re",
    "redirect_stderr",
    "shutil",
    "sys",
    "typer",
    "urlparse",
    # package metadata
    "__version__",
    # application and command groups
    "app",
    "auth_app",
    "autopilot_app",
    "config_app",
    "cron_app",
    "main",
    "mcp_app",
    "plugin_app",
    "provider_app",
    # dry-run preview helpers
    "_PREVIEW_STOPWORDS",
    "_build_dry_run_preview",
    "_candidate_entry",
    "_dry_run_command_behavior",
    "_evaluate_dry_run_readiness",
    "_format_dry_run_preview",
    "_mcp_transport_preview",
    "_recommend_preview_candidates",
    "_safe_short",
    "_schema_argument_preview",
    "_score_candidate_match",
    "_tokenize_preview_text",
    "_validate_mcp_server",
    "_version_callback",
    # prompt helpers and their legacy private aliases
    "can_use_questionary",
    "confirm_prompt",
    "secret_prompt",
    "select_from_menu",
    "select_with_questionary",
    "text_prompt",
    "_can_use_questionary",
    "_confirm_prompt",
    "_secret_prompt",
    "_select_from_menu",
    "_select_with_questionary",
    "_text_prompt",
    # mcp commands
    "mcp_add",
    "mcp_list",
    "mcp_remove",
    # plugin commands
    "plugin_install",
    "plugin_list",
    "plugin_uninstall",
    # cron commands
    "cron_history_cmd",
    "cron_list_cmd",
    "cron_logs_cmd",
    "cron_start",
    "cron_status_cmd",
    "cron_stop",
    "cron_toggle_cmd",
    # autopilot commands
    "autopilot_add_cmd",
    "autopilot_context_cmd",
    "autopilot_export_dashboard_cmd",
    "autopilot_install_cron_cmd",
    "autopilot_journal_cmd",
    "autopilot_list_cmd",
    "autopilot_run_next_cmd",
    "autopilot_scan_cmd",
    "autopilot_status_cmd",
    "autopilot_tick_cmd",
    # setup helpers and command
    "_configure_custom_profile_via_setup",
    "_default_credential_slot_for_profile",
    "_ensure_preset_profile",
    "_ensure_profile_auth",
    "_format_profile_choice_label",
    "_maybe_update_profile_auth",
    "_prompt_api_key_for_profile",
    "_prompt_model_for_profile",
    "_select_setup_workflow",
    "_specialize_setup_target",
    "_styled_missing_suffix",
    "setup_cmd",
    # auth helpers and commands
    "_AUTH_SOURCE_LABELS",
    "_PROVIDER_LABELS",
    "_bind_external_provider",
    "_login_provider",
    "_maybe_update_default_model_for_provider",
    "_run_copilot_login",
    "auth_claude_login",
    "auth_codex_login",
    "auth_copilot_login",
    "auth_copilot_logout",
    "auth_login",
    "auth_logout",
    "auth_status_cmd",
    "auth_switch",
    # config helpers and commands
    "_config_coerce_value",
    "_config_resolve_target",
    "config_set",
    "config_show",
    # provider commands
    "provider_add",
    "provider_edit",
    "provider_list",
    "provider_remove",
    "provider_use",
]

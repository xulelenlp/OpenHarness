"""Project-level repo autopilot state, intake, and execution helpers."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha1
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from openharness.autopilot.service.constants import (
    _BUG_HINTS,
    _DEFAULT_AUTOPILOT_POLICY,
    _DEFAULT_RELEASE_POLICY,
    _DEFAULT_VERIFICATION_POLICY,
    _SOURCE_BASE_SCORES,
    _URGENT_HINTS,
)
from openharness.autopilot.service.dashboard import DashboardMixin
from openharness.autopilot.service.git_ops import GitOpsMixin
from openharness.autopilot.service.helpers import (
    _SHELL_METACHARS,
    _bilingual_lines,
    _json_default,
    _looks_available,
    _parse_verification_entry,
    _safe_text,
    _shorten,
    _source_ref_number,
    _VerificationCommand,
)
from openharness.autopilot.service.runner import RunnerMixin
from openharness.autopilot.service.store import StoreMixin
from openharness.autopilot.service.verification import VerificationMixin
from openharness.autopilot.types import (
    RepoAutopilotRegistry,
    RepoJournalEntry,
    RepoRunResult,
    RepoTaskCard,
    RepoTaskSource,
    RepoTaskStatus,
    RepoVerificationStep,
)
from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_autopilot_policy_path,
    get_project_autopilot_registry_path,
    get_project_autopilot_runs_dir,
    get_project_release_policy_path,
    get_project_repo_journal_path,
    get_project_verification_policy_path,
)
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
)
from openharness.swarm.worktree import WorktreeManager
from openharness.utils.fs import atomic_write_text


class RepoAutopilotStore(
    StoreMixin,
    RunnerMixin,
    GitOpsMixin,
    DashboardMixin,
    VerificationMixin,
):
    """Persist and query project-level autopilot state."""


__all__ = [
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "Any",
    "ErrorEvent",
    "Path",
    "RepoAutopilotRegistry",
    "RepoAutopilotStore",
    "RepoJournalEntry",
    "RepoRunResult",
    "RepoTaskCard",
    "RepoTaskSource",
    "RepoTaskStatus",
    "RepoVerificationStep",
    "WorktreeManager",
    "_BUG_HINTS",
    "_DEFAULT_AUTOPILOT_POLICY",
    "_DEFAULT_RELEASE_POLICY",
    "_DEFAULT_VERIFICATION_POLICY",
    "_SHELL_METACHARS",
    "_SOURCE_BASE_SCORES",
    "_URGENT_HINTS",
    "_VerificationCommand",
    "_bilingual_lines",
    "_json_default",
    "_looks_available",
    "_parse_verification_entry",
    "_safe_text",
    "_shorten",
    "_source_ref_number",
    "asyncio",
    "atomic_write_text",
    "dataclass",
    "escape",
    "get_project_active_repo_context_path",
    "get_project_autopilot_policy_path",
    "get_project_autopilot_registry_path",
    "get_project_autopilot_runs_dir",
    "get_project_release_policy_path",
    "get_project_repo_journal_path",
    "get_project_verification_policy_path",
    "json",
    "os",
    "sha1",
    "shlex",
    "subprocess",
    "tempfile",
    "time",
    "uuid4",
    "yaml",
]

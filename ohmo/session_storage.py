"""Session persistence for ``ohmo``.

The on-disk format and read/write logic live in
:mod:`openharness.services.session_storage`; this module only supplies the
ohmo-specific directory (``<workspace>/sessions``) and metadata (``app`` and
``session_key``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ohmo.workspace import get_sessions_dir
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage
from openharness.services.session_backend import SessionBackend
from openharness.services.session_storage import (
    export_transcript,
    list_snapshots_in,
    load_snapshot_by_id_in,
    load_snapshot_file,
    write_snapshot,
)
from openharness.services.session_storage import (
    get_session_key_latest_path as _session_key_latest_path,
)

APP_NAME = "ohmo"


def get_session_dir(workspace: str | Path | None = None) -> Path:
    """Return the ohmo sessions directory."""
    session_dir = get_sessions_dir(workspace)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_session_key_latest_path(workspace: str | Path | None, session_key: str) -> Path:
    """Return the per-session-key ``latest-<hash>.json`` path.

    qaapp uses ``session_key=conversation_id``; exposing the path lets
    maintenance code delete the right snapshot without duplicating the hash
    algorithm.
    """
    return _session_key_latest_path(get_session_dir(workspace), session_key)


def save_session_snapshot(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    session_key: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist the latest ohmo session snapshot."""
    return write_snapshot(
        get_session_dir(workspace),
        cwd=cwd,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        usage=usage,
        session_id=session_id,
        session_key=session_key,
        app=APP_NAME,
        tool_metadata=tool_metadata,
    )


def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
    return load_snapshot_file(get_session_dir(workspace) / "latest.json")


def load_latest_for_session_key(
    workspace: str | Path | None,
    session_key: str,
) -> dict[str, Any] | None:
    return load_snapshot_file(get_session_key_latest_path(workspace, session_key))


def list_snapshots(workspace: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    return list_snapshots_in(get_session_dir(workspace), limit=limit, include_latest=False)


def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
    return load_snapshot_by_id_in(get_session_dir(workspace), session_id, include_latest=True)


def export_session_markdown(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    messages: list[ConversationMessage],
) -> Path:
    return export_transcript(
        get_session_dir(workspace),
        title="ohmo Session Transcript",
        messages=messages,
        include_tool_blocks=False,
    )


class OhmoSessionBackend(SessionBackend):
    """Session backend rooted in ``.ohmo/sessions``."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = workspace

    def get_session_dir(self, cwd: str | Path) -> Path:
        return get_session_dir(self._workspace)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        session_key: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return save_session_snapshot(
            cwd=cwd,
            workspace=self._workspace,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            session_key=session_key,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        return load_latest(self._workspace)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        return list_snapshots(self._workspace, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        return load_by_id(self._workspace, session_id)

    def load_latest_for_session_key(self, session_key: str) -> dict[str, Any] | None:
        return load_latest_for_session_key(self._workspace, session_key)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        return export_session_markdown(
            cwd=cwd,
            workspace=self._workspace,
            messages=messages,
        )

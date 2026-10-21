"""Personal memory helpers for ``.ohmo``.

The project memory store and ohmo's personal store share the lifecycle
implementation in :mod:`openharness.memory.manager`; this module only binds
it to the ``.ohmo`` workspace and personal defaults.
"""

from __future__ import annotations

from pathlib import Path

from ohmo.workspace import get_memory_dir, get_memory_index_path
from openharness.commands import MemoryCommandBackend
from openharness.memory.manager import (
    MemoryLocation,
    add_memory_entry_at,
    list_memory_files_at,
    soft_delete_memory_entry,
)


def _location(workspace: str | Path | None) -> MemoryLocation:
    memory_dir = get_memory_dir(workspace)
    return MemoryLocation(
        scan_cwd=_scan_cwd(workspace, memory_dir),
        memory_dir=memory_dir,
        entrypoint=get_memory_index_path(workspace),
        default_type="personal",
        default_category="preference",
    )


def list_memory_files(workspace: str | Path | None = None) -> list[Path]:
    """List ``.ohmo`` memory markdown files."""
    return list_memory_files_at(_location(workspace))


def add_memory_entry(workspace: str | Path | None, title: str, content: str) -> Path:
    """Create a personal memory file and append it to ``MEMORY.md``."""
    return add_memory_entry_at(
        _location(workspace),
        title,
        content,
        include_tags=False,
    )


def remove_memory_entry(workspace: str | Path | None, name: str) -> bool:
    """Soft-delete a personal memory file and remove its index entry."""
    return soft_delete_memory_entry(_location(workspace), name)


def load_memory_prompt(workspace: str | Path | None = None, *, max_files: int = 5) -> str | None:
    """Return a prompt section describing personal memory."""
    memory_dir = get_memory_dir(workspace)
    index_path = get_memory_index_path(workspace)
    lines = [
        "# ohmo Memory",
        f"- Personal memory directory: {memory_dir}",
        "- Use this memory for stable user preferences and durable personal context.",
    ]

    if index_path.exists():
        index_lines = index_path.read_text(encoding="utf-8").splitlines()[:200]
        lines.extend(["", "## MEMORY.md", "```md", *index_lines, "```"])

    for path in list_memory_files(workspace)[:max_files]:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue
        lines.extend(["", f"## {path.name}", "```md", content[:4000], "```"])

    return "\n".join(lines)


def create_memory_command_backend(workspace: str | Path | None = None) -> MemoryCommandBackend:
    """Return a ``/memory`` backend bound to ohmo's personal memory store."""

    return MemoryCommandBackend(
        label="ohmo personal memory",
        default_type="personal",
        default_category="preference",
        get_memory_dir=lambda: get_memory_dir(workspace),
        get_entrypoint=lambda: get_memory_index_path(workspace),
        list_files=lambda: list_memory_files(workspace),
        add_entry=lambda title, content: add_memory_entry(workspace, title, content),
        remove_entry=lambda name: remove_memory_entry(workspace, name),
    )


def _scan_cwd(workspace: str | Path | None, memory_dir: Path) -> Path:
    return Path(workspace) if workspace is not None else memory_dir.parent

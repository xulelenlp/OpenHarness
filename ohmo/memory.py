"""Personal memory helpers for ``.ohmo``."""

from __future__ import annotations

from pathlib import Path
from re import sub

from ohmo.workspace import get_memory_dir, get_memory_index_path
from openharness.commands import MemoryCommandBackend
from openharness.memory.manager import (
    MemoryLocation,
    effective_memory_signature,
    next_memory_path,
    soft_delete_memory_entry,
)
from openharness.memory.scan import scan_memory_files
from openharness.memory.schema import (
    SCHEMA_VERSION,
    coerce_int,
    compute_memory_signature,
    first_content_line,
    format_datetime,
    generate_memory_id,
    memory_metadata_from_path,
    render_memory_file,
    split_memory_file,
    utc_now,
)
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text


def list_memory_files(workspace: str | Path | None = None) -> list[Path]:
    """List ``.ohmo`` memory markdown files."""
    memory_dir = get_memory_dir(workspace)
    return sorted(
        header.path
        for header in scan_memory_files(
            _scan_cwd(workspace, memory_dir),
            max_files=None,
            memory_dir=memory_dir,
        )
    )


def add_memory_entry(workspace: str | Path | None, title: str, content: str) -> Path:
    """Create a personal memory file and append it to ``MEMORY.md``."""
    memory_dir = get_memory_dir(workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    with exclusive_file_lock(memory_dir / ".memory.lock"):
        memory_type = "personal"
        category = "preference"
        body = content.strip() + "\n"
        signature = compute_memory_signature(body, memory_type, category)
        existing = scan_memory_files(
            _scan_cwd(workspace, memory_dir),
            max_files=None,
            include_disabled=True,
            include_expired=True,
            memory_dir=memory_dir,
        )
        duplicate = next(
            (
                header
                for header in existing
                if effective_memory_signature(header.path, header.signature) == signature
            ),
            None,
        )
        path = duplicate.path if duplicate is not None else next_memory_path(memory_dir, slug)
        now = utc_now()
        now_text = format_datetime(now)
        if path.exists():
            metadata, old_body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
            metadata = memory_metadata_from_path(
                path,
                metadata,
                old_body,
                now=now,
                source=str(metadata.get("source") or "manual"),
                default_type=memory_type,
                default_category=category,
            )
            created_at = str(metadata.get("created_at") or now_text)
            memory_id = str(metadata.get("id") or generate_memory_id(now))
        else:
            metadata = {}
            created_at = now_text
            memory_id = generate_memory_id(now)
        metadata.update(
            {
                "schema_version": SCHEMA_VERSION,
                "id": memory_id,
                "name": title.strip(),
                "description": first_content_line(body) or title.strip(),
                "type": str(metadata.get("type") or memory_type),
                "category": str(metadata.get("category") or category),
                "importance": max(coerce_int(metadata.get("importance"), default=0), 1),
                "source": "manual",
                "signature": signature,
                "created_at": created_at,
                "updated_at": now_text,
                "ttl_days": metadata.get("ttl_days"),
                "disabled": False,
                "supersedes": metadata.get("supersedes") or [],
            }
        )
        atomic_write_text(path, render_memory_file(metadata, body))

        index_path = get_memory_index_path(workspace)
        existing_index = index_path.read_text(encoding="utf-8") if index_path.exists() else "# Memory Index\n"
        if path.name not in existing_index:
            existing_index = existing_index.rstrip() + f"\n- [{title}]({path.name})\n"
            atomic_write_text(index_path, existing_index)
    return path


def remove_memory_entry(workspace: str | Path | None, name: str) -> bool:
    """Soft-delete a personal memory file and remove its index entry."""
    memory_dir = get_memory_dir(workspace)
    return soft_delete_memory_entry(
        MemoryLocation(
            scan_cwd=_scan_cwd(workspace, memory_dir),
            memory_dir=memory_dir,
            entrypoint=get_memory_index_path(workspace),
            default_type="personal",
            default_category="preference",
        ),
        name,
    )


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

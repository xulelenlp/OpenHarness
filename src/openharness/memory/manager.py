"""Helpers for managing memory files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from re import sub

from openharness.memory.paths import get_memory_entrypoint, get_project_memory_dir
from openharness.memory.scan import scan_memory_files
from openharness.memory.schema import (
    DEFAULT_MEMORY_SCOPE,
    DEFAULT_MEMORY_TYPE,
    SCHEMA_VERSION,
    MemoryScope,
    MemoryType,
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


@dataclass(frozen=True)
class MemoryLocation:
    """Where a memory store lives.

    Project memory and ohmo personal memory share the same file format and
    soft-delete workflow, but have different directories, entrypoints, and
    default type/category values.  This small value object lets both layers
    reuse one lifecycle implementation.
    """

    scan_cwd: Path
    memory_dir: Path
    entrypoint: Path
    default_type: str = DEFAULT_MEMORY_TYPE
    default_category: str = "knowledge"


def next_memory_path(memory_dir: Path, slug: str) -> Path:
    """Return a non-conflicting ``<slug>.md`` path in *memory_dir*."""
    path = memory_dir / f"{slug}.md"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = memory_dir / f"{slug}_{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def effective_memory_signature(path: Path, existing_signature: str) -> str:
    """Return the stored signature, or recompute it for legacy files."""
    if existing_signature:
        return existing_signature
    try:
        metadata, body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    memory_type = str(metadata.get("type") or "personal")
    category = str(metadata.get("category") or "preference")
    return compute_memory_signature(body, memory_type, category)


def soft_delete_memory_entry(location: MemoryLocation, name: str) -> bool:
    """Soft-delete a memory file and remove its index entry.

    Shared by project memory (``openharness.memory``) and personal memory
    (``ohmo.memory``) so the two stores cannot drift in how they remove
    entries.
    """
    matches = [
        header
        for header in scan_memory_files(
            location.scan_cwd,
            max_files=None,
            include_disabled=True,
            include_expired=True,
            memory_dir=location.memory_dir,
        )
        if name in {header.path.stem, header.path.name, header.title, header.id}
    ]
    if not matches:
        return False
    header = matches[0]
    if header.disabled:
        return False
    path = header.path
    with exclusive_file_lock(location.memory_dir / ".memory.lock"):
        if path.exists():
            content = path.read_text(encoding="utf-8")
            metadata, body, _, _ = split_memory_file(content)
            metadata = memory_metadata_from_path(
                path,
                metadata,
                body,
                source="manual",
                default_type=location.default_type,
                default_category=location.default_category,
            )
            metadata["disabled"] = True
            metadata["updated_at"] = format_datetime(utc_now())
            atomic_write_text(path, render_memory_file(metadata, body))

        entrypoint = location.entrypoint
        if entrypoint.exists():
            lines = [
                line
                for line in entrypoint.read_text(encoding="utf-8").splitlines()
                if path.name not in line
            ]
            atomic_write_text(entrypoint, "\n".join(lines).rstrip() + "\n")
    return True


def list_memory_files(cwd: str | Path) -> list[Path]:
    """List memory markdown files for the project."""
    return sorted(header.path for header in scan_memory_files(cwd, max_files=None))


def list_memory_files_at(location: MemoryLocation) -> list[Path]:
    """List memory markdown files in an explicit memory location."""
    return sorted(
        header.path
        for header in scan_memory_files(
            location.scan_cwd,
            max_files=None,
            memory_dir=location.memory_dir,
        )
    )


def add_memory_entry_at(
    location: MemoryLocation,
    title: str,
    content: str,
    *,
    memory_type: str | None = None,
    scope: str | None = None,
    description: str = "",
    tags: tuple[str, ...] = (),
    include_tags: bool = True,
) -> Path:
    """Create or refresh a memory file in *location* and append it to its index.

    The project store and ohmo's personal store share this implementation;
    they differ only in directory, index file, and default type/category.
    """
    memory_dir = location.memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)
    effective_type = memory_type or location.default_type
    category = location.default_category
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    with exclusive_file_lock(memory_dir / ".memory.lock"):
        body = content.strip() + "\n"
        signature = compute_memory_signature(body, effective_type, category)
        existing = scan_memory_files(
            location.scan_cwd,
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
                default_type=effective_type,
                default_category=category,
            )
            created_at = str(metadata.get("created_at") or now_text)
            memory_id = str(metadata.get("id") or generate_memory_id(now))
        else:
            metadata = {}
            created_at = now_text
            memory_id = generate_memory_id(now)

        updated = {
            "schema_version": SCHEMA_VERSION,
            "id": memory_id,
            "name": title.strip(),
            "description": description.strip() or first_content_line(body) or title.strip(),
            "type": str(metadata.get("type") or effective_type),
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
        if scope is not None:
            updated["scope"] = str(metadata.get("scope") or scope)
        if include_tags:
            updated["tags"] = list(
                dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip())
            )
        metadata.update(updated)
        atomic_write_text(path, render_memory_file(metadata, body))

        entrypoint = location.entrypoint
        index_text = entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else "# Memory Index\n"
        if path.name not in index_text:
            index_text = index_text.rstrip() + f"\n- [{title}]({path.name})\n"
            atomic_write_text(entrypoint, index_text)
    return path


def add_memory_entry(
    cwd: str | Path,
    title: str,
    content: str,
    *,
    memory_type: MemoryType = DEFAULT_MEMORY_TYPE,
    scope: MemoryScope = DEFAULT_MEMORY_SCOPE,
    description: str = "",
    tags: tuple[str, ...] = (),
) -> Path:
    """Create or refresh a memory file and append it to MEMORY.md."""
    if scope == "team":
        from openharness.memory.team import check_team_memory_secrets, ensure_team_memory_vault

        secret_error = check_team_memory_secrets(content)
        if secret_error:
            raise ValueError(secret_error)
        memory_dir = ensure_team_memory_vault(cwd)
    else:
        memory_dir = get_project_memory_dir(cwd)
    location = MemoryLocation(
        scan_cwd=Path(cwd),
        memory_dir=memory_dir,
        entrypoint=memory_dir / "MEMORY.md",
    )
    return add_memory_entry_at(
        location,
        title,
        content,
        memory_type=memory_type,
        scope=scope,
        description=description,
        tags=tags,
    )


def remove_memory_entry(cwd: str | Path, name: str) -> bool:
    """Soft-delete a project memory file and remove its index entry."""
    return soft_delete_memory_entry(
        MemoryLocation(
            scan_cwd=Path(cwd),
            memory_dir=get_project_memory_dir(cwd),
            entrypoint=get_memory_entrypoint(cwd),
        ),
        name,
    )

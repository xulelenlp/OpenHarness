"""Conversation workspace file primitives."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from uuid import uuid4

from qaapp.workspace.files.errors import AttachmentTooLarge, UnsupportedAttachment
from qaapp.workspace.files.paths import (
    _RESERVED_STEMS,
    _UNSAFE_CHARS,
    MAX_FILENAME_CHARS,
    ConversationPaths,
)


def sanitize_filename(name: str, *, fallback: str = "file") -> str:
    """清洗上传文件名：去掉路径、控制字符与危险片段，保留后缀。"""
    raw = unicodedata.normalize("NFC", str(name or ""))
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_CHARS.sub("_", base)
    # 先剥掉首尾的点（"." / ".." / "..." 这类名字直接回退），再把中间残留的
    # ".." 换成下划线，避免任何形式的相对路径片段留在文件名里。
    base = re.sub(r"\s+", " ", base).strip().strip(".")
    base = base.replace("..", "_")
    if not base:
        return fallback
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    stem = stem.strip().strip(".") or fallback
    if stem.lower() in _RESERVED_STEMS:
        stem = f"_{stem}"
    keep = max(1, MAX_FILENAME_CHARS - len(suffix) - 1)
    stem = stem[:keep]
    return f"{stem}.{suffix}" if suffix else stem


def unique_path(directory: Path, filename: str) -> Path:
    """在目录内取一个不冲突的路径（重名追加 -1/-2…）。"""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, dot, suffix = filename.rpartition(".")
    if not dot:
        stem, suffix = filename, ""
    for index in range(1, 1000):
        name = f"{stem}-{index}.{suffix}" if suffix else f"{stem}-{index}"
        candidate = directory / name
        if not candidate.exists():
            return candidate
    return directory / f"{uuid4().hex[:8]}-{filename}"


def ensure_supported(name: str, allowed_suffixes: Iterable[str]) -> str:
    """校验后缀在白名单内，返回小写后缀。"""
    suffix = Path(sanitize_filename(name)).suffix.lower()
    allowed = {item.lower() for item in allowed_suffixes}
    if suffix not in allowed:
        raise UnsupportedAttachment(
            f"不支持的文件类型「{suffix or '无后缀'}」，当前允许："
            + "、".join(sorted(allowed))
        )
    return suffix


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


async def save_upload(
    paths: ConversationPaths,
    filename: str,
    chunks: AsyncIterator[bytes],
    *,
    max_bytes: int,
) -> tuple[Path, int]:
    """流式写盘，超过 ``max_bytes`` 立即中止并删除半成品。"""
    sanitized = sanitize_filename(filename)
    target = unique_path(paths.uploads, sanitized)
    size = 0
    try:
        with target.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise AttachmentTooLarge(
                        f"文件「{sanitized}」超过大小上限 {max_bytes // (1024 * 1024)}MB"
                    )
                await asyncio.to_thread(handle.write, chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target, size

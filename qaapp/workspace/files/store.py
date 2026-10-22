"""Conversation workspace file primitives."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from openharness.utils.fs import atomic_write_text
from qaapp.workspace.files.errors import AttachmentNotFound, TooManyAttachments
from qaapp.workspace.files.paths import ConversationPaths


def new_attachment_id() -> str:
    return f"att_{uuid4().hex[:12]}"


class AttachmentStore:
    """``attachments.json`` 的读写（同会话内用锁串行化）。"""

    def __init__(self, paths: ConversationPaths) -> None:
        self._paths = paths
        self._lock = asyncio.Lock()

    @property
    def paths(self) -> ConversationPaths:
        return self._paths

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._paths.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        records = data.get("attachments") if isinstance(data, dict) else None
        if not isinstance(records, dict):
            return {}
        return {
            str(key): value
            for key, value in records.items()
            if isinstance(value, dict)
        }

    def save(self, records: dict[str, dict[str, Any]]) -> None:
        self._paths.ensure()
        payload = {"conversation_id": self._paths.conversation_id, "attachments": records}
        atomic_write_text(
            self._paths.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def get(self, attachment_id: str) -> dict[str, Any] | None:
        return self.load().get(attachment_id)

    def list(self) -> list[dict[str, Any]]:
        records = list(self.load().values())
        records.sort(key=lambda item: float(item.get("uploaded_at") or 0))
        return records

    def count(self) -> int:
        return len(self.load())

    async def add(self, record: dict[str, Any], *, max_files: int) -> None:
        async with self._lock:
            records = self.load()
            if len(records) >= max_files:
                raise TooManyAttachments(
                    f"本会话附件数量已达上限（{max_files} 个），请新建会话或清理旧文件"
                )
            records[str(record["attachment_id"])] = record
            self.save(records)

    async def remove(self, attachment_id: str) -> bool:
        async with self._lock:
            records = self.load()
            if attachment_id not in records:
                return False
            records.pop(attachment_id, None)
            self.save(records)
            return True

    def resolve(self, attachment_ids: Iterable[str]) -> list[dict[str, Any]]:
        """按 id 取出记录（保持传入顺序、去重）；缺失的 id 抛 ``AttachmentNotFound``。"""
        records = self.load()
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in attachment_ids:
            attachment_id = str(raw).strip()
            if not attachment_id or attachment_id in seen:
                continue
            seen.add(attachment_id)
            record = records.get(attachment_id)
            if record is None:
                raise AttachmentNotFound(f"附件不存在或不属于本会话：{attachment_id}")
            resolved.append(record)
        return resolved


def build_record(
    *,
    attachment_id: str,
    name: str,
    stored_path: str,
    extracted_path: str,
    size: int,
    kind: str,
    parse_status: str,
    parse_error: str = "",
    text_chars: int = 0,
    preview: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装一条对外（也是落盘）的附件记录。"""
    return {
        "attachment_id": attachment_id,
        "name": name,
        "kind": kind,
        "size": size,
        "uploaded_at": time.time(),
        "parse_status": parse_status,
        "parse_error": parse_error,
        "text_chars": text_chars,
        "preview": preview,
        "stored_path": stored_path,
        "extracted_path": extracted_path,
        "meta": meta or {},
    }


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    """给前端用的精简记录（不带解析全文，只带节选）。"""
    return {
        key: record.get(key)
        for key in (
            "attachment_id",
            "name",
            "kind",
            "size",
            "uploaded_at",
            "parse_status",
            "parse_error",
            "text_chars",
            "stored_path",
            "extracted_path",
            "meta",
        )
    }

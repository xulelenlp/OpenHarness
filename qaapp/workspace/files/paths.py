"""Conversation workspace file primitives."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ohmo.workspace import get_workspace_root
from openharness.utils.fs import atomic_write_text
from qaapp.config import ConversationWorkspaceConfig

SUBDIRS = ("uploads", "extracted", "scripts", "tmp", "outputs")
INDEX_FILENAME = "attachments.json"
META_FILENAME = "meta.json"

#: 会话 ID 由服务端生成（uuid4 hex 前 12 位），也是会话目录名的白名单：
#: 只允许这个形状，杜绝 ``../`` 之类的路径穿越。维护/清理逻辑复用同一个常量。
CONVERSATION_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

#: 文件名最大长度（字符），超出截断保留后缀。
MAX_FILENAME_CHARS = 120

#: Windows 保留名，避免用户把文件拷回 Windows 时出错。
_RESERVED_STEMS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")

def conversations_root(
    workspace: str | Path | None,
    config: ConversationWorkspaceConfig | None = None,
) -> Path:
    """返回「所有会话目录」的根。"""
    configured = (config.root if config else "") or ""
    if configured.strip():
        return Path(configured).expanduser().resolve()
    return get_workspace_root(workspace) / "conversations"


@dataclass(frozen=True)
class ConversationPaths:
    """一个会话的目录布局（只做路径计算，不自动建目录）。"""

    conversation_id: str
    root: Path

    @classmethod
    def for_conversation(
        cls,
        workspace: str | Path | None,
        conversation_id: str,
        config: ConversationWorkspaceConfig | None = None,
    ) -> ConversationPaths:
        root = conversations_root(workspace, config) / conversation_id
        return cls(conversation_id=conversation_id, root=root)

    # -- 子目录 ---------------------------------------------------------

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def extracted(self) -> Path:
        return self.root / "extracted"

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILENAME

    @property
    def meta_path(self) -> Path:
        return self.root / META_FILENAME

    # -- 生命周期 -------------------------------------------------------

    def ensure(self) -> ConversationPaths:
        """幂等创建目录结构与 meta.json。"""
        for name in SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        if not self.meta_path.exists():
            self.write_meta({"conversation_id": self.conversation_id, "created_at": time.time()})
        return self

    def write_meta(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        if self.meta_path.exists():
            try:
                existing = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    existing.update(payload)
                    payload = existing
            except (OSError, json.JSONDecodeError):
                pass
        atomic_write_text(self.meta_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def read_meta(self) -> dict[str, Any]:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def touch_turn(self) -> None:
        self.write_meta({"last_turn_at": time.time()})

    # -- 路径安全 -------------------------------------------------------

    def relative(self, path: str | Path) -> str:
        """把目录内路径转成相对 POSIX 路径（对外接口一律用它）。"""
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()

    def resolve_within(self, relative_path: str) -> Path:
        """把相对路径解析到会话目录内；越界/绝对路径抛 ``ValueError``。"""
        candidate = Path(str(relative_path).strip())
        if not str(relative_path).strip():
            raise ValueError("路径不能为空")
        if candidate.is_absolute():
            raise ValueError(f"只接受会话目录内的相对路径：{relative_path}")
        target = (self.root / candidate).resolve()
        root = self.root.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"路径越界：{relative_path}") from exc
        return target

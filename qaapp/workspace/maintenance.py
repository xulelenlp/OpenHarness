"""qaapp 工作区维护：会话目录 / 快照 / 轨迹的统一清理。

**为什么要单独一个模块**：以前清理只能靠“手工删 ``~/.ohmo/conversations/<id>``”，
``webapp.json`` 里的 ``conversation.max_active_bundles`` / ``idle_timeout_seconds``
也只是预留字段。随着附件、脚本、产物都落在会话目录，长期运行会持续膨胀，且手工删除
容易漏掉对应的 session 快照与 JSONL 轨迹。

本模块只做一件事：**先算出“要删什么”，再由调用方显式 apply**。默认 dry-run，
所有删除都经过路径白名单校验，不跟随符号链接。

只清理 **qaapp 自己的** 数据：

- ``<conversations_root>/<12位会话ID>/``
- 对应的 ``sessions/session-<id>.json``、``sessions/latest-<hash>.json``
- 对应的 ``sessions/traces/<id>.jsonl``
- 会话目录已不存在的孤儿 trace（只处理 12 位 ID 形状的文件名）

``sessions/session-*.json`` 里可能还有 ohmo（个人助手）的会话，因此**不会**按“孤儿快照”
批量删除；只删与本次清理的 qaapp 会话 ID 精确对应的文件。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ohmo.session_storage import get_session_key_latest_path
from ohmo.workspace import get_sessions_dir, get_workspace_root
from qaapp.config import WebappConfig
from qaapp.utils.formatting import format_bytes
from qaapp.workspace.files import (
    CONVERSATION_ID_PATTERN,
    META_FILENAME,
    conversations_root,
)
from qaapp.workspace.trace import TRACES_DIRNAME

#: 单次报告最多列多少条明细，避免 CLI 被几百行刷屏。
REPORT_LIMIT = 50


@dataclass(frozen=True)
class CleanupItem:
    """一个待清理项（文件或会话目录）。"""

    kind: str
    path: Path
    conversation_id: str = ""
    size: int = 0
    mtime: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "conversation_id": self.conversation_id,
            "size": self.size,
            "mtime": self.mtime,
            "reason": self.reason,
        }


@dataclass
class CleanupPlan:
    """一次清理的完整计划（不产生任何删除）。"""

    workspace: Path
    conversations_root: Path
    sessions_root: Path
    traces_dir: Path
    older_than_seconds: float
    keep_latest: int
    items: list[CleanupItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def conversation_ids(self) -> list[str]:
        """本次计划真正要删除的会话目录 ID（不含孤儿 trace 的 ID）。"""
        return sorted(
            {
                item.conversation_id
                for item in self.items
                if item.kind == "conversation" and item.conversation_id
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "conversations_root": str(self.conversations_root),
            "sessions_root": str(self.sessions_root),
            "older_than_seconds": self.older_than_seconds,
            "keep_latest": self.keep_latest,
            "total_bytes": self.total_bytes,
            "conversation_ids": self.conversation_ids,
            "items": [item.to_dict() for item in self.items],
            "skipped": list(self.skipped),
        }


@dataclass
class CleanupResult:
    """实际删除结果。"""

    removed: list[CleanupItem] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        return sum(item.size for item in self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed": [item.to_dict() for item in self.removed],
            "failed": list(self.failed),
            "removed_count": len(self.removed),
            "freed_bytes": self.freed_bytes,
        }


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


def _read_activity(conversation_dir: Path) -> float:
    """会话最后活跃时间：优先 meta.json，其次目录 mtime。"""
    meta_path = conversation_dir / META_FILENAME
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("last_turn_at", "created_at"):
                value = data.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
    except (OSError, json.JSONDecodeError):
        pass
    try:
        return conversation_dir.stat().st_mtime
    except OSError:
        return 0.0


def _dir_size(root: Path) -> int:
    """递归统计目录大小；跳过符号链接，避免统计到目录外。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _file_size(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_file():
            return 0
        return path.stat().st_size
    except OSError:
        return 0


def _add_file(
    items: list[CleanupItem],
    seen: set[Path],
    path: Path,
    *,
    kind: str,
    conversation_id: str,
    reason: str,
) -> None:
    if path in seen or not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        return
    seen.add(path)
    items.append(
        CleanupItem(
            kind=kind,
            path=path,
            conversation_id=conversation_id,
            size=_file_size(path),
            mtime=path.stat().st_mtime,
            reason=reason,
        )
    )


def _conversation_entries(
    root: Path, skipped: list[str]
) -> list[tuple[str, Path, float]]:
    """返回合法会话目录 [(id, path, last_activity)]，按活跃时间降序。"""
    if not root.is_dir():
        return []
    entries: list[tuple[str, Path, float]] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            skipped.append(f"跳过符号链接：{child}")
            continue
        if not child.is_dir():
            continue
        if not CONVERSATION_ID_PATTERN.match(child.name):
            skipped.append(f"跳过非会话目录：{child}")
            continue
        entries.append((child.name, child, _read_activity(child)))
    entries.sort(key=lambda item: item[2], reverse=True)
    return entries


def _validate_root(root: Path, workspace: Path) -> None:
    """拒绝明显危险的清理根（``/``、工作区根本身）。"""
    resolved = root.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError(f"拒绝清理文件系统根目录：{resolved}")
    if resolved == workspace.resolve():
        raise ValueError(f"拒绝把工作区根当作会话目录根：{resolved}")


def build_cleanup_plan(
    workspace: str | Path | None,
    webapp: WebappConfig,
    *,
    older_than_seconds: float | None = None,
    keep_latest: int | None = None,
    include_orphan_traces: bool = True,
) -> CleanupPlan:
    """计算清理计划。

    - ``older_than_seconds`` 默认取 ``webapp.conversation.idle_timeout_seconds``；
    - ``keep_latest`` 默认取 ``webapp.conversation.max_active_bundles``；
    - 只有“超过闲置阈值 **且** 不在最近 N 个会话里”的会话才会被清理；
    - 默认包含孤儿 trace（会话目录已不存在、但轨迹还在）。
    """
    ws = get_workspace_root(workspace)
    conv_root = conversations_root(workspace, webapp.conversation_workspace)
    sessions_root = get_sessions_dir(workspace)
    traces_dir = sessions_root / TRACES_DIRNAME

    _validate_root(conv_root, ws)

    if older_than_seconds is None:
        older_than_seconds = float(webapp.conversation.idle_timeout_seconds)
    if keep_latest is None:
        keep_latest = int(webapp.conversation.max_active_bundles)
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds 不能为负数")
    if keep_latest < 0:
        raise ValueError("keep_latest 不能为负数")

    plan = CleanupPlan(
        workspace=ws,
        conversations_root=conv_root,
        sessions_root=sessions_root,
        traces_dir=traces_dir,
        older_than_seconds=older_than_seconds,
        keep_latest=keep_latest,
    )

    entries = _conversation_entries(conv_root, plan.skipped)
    keep_ids = {cid for cid, _path, _activity in entries[:keep_latest]}
    now = time.time()
    seen: set[Path] = set()

    for cid, path, activity in entries:
        if cid in keep_ids:
            continue
        idle = now - activity
        if idle < older_than_seconds:
            continue
        plan.items.append(
            CleanupItem(
                kind="conversation",
                path=path,
                conversation_id=cid,
                size=_dir_size(path),
                mtime=activity,
                reason=f"闲置 {idle / 86400:.1f} 天，且不在最近 {keep_latest} 个会话内",
            )
        )
        _add_file(
            plan.items,
            seen,
            sessions_root / f"session-{cid}.json",
            kind="snapshot",
            conversation_id=cid,
            reason="对应会话目录将被清理",
        )
        _add_file(
            plan.items,
            seen,
            get_session_key_latest_path(workspace, cid),
            kind="latest_snapshot",
            conversation_id=cid,
            reason="对应会话目录将被清理",
        )
        _add_file(
            plan.items,
            seen,
            traces_dir / f"{cid}.jsonl",
            kind="trace",
            conversation_id=cid,
            reason="对应会话目录将被清理",
        )

    if include_orphan_traces and traces_dir.is_dir():
        for trace in sorted(traces_dir.glob("*.jsonl")):
            cid = trace.stem
            if not CONVERSATION_ID_PATTERN.match(cid):
                continue
            if trace in seen or (conv_root / cid).is_dir():
                continue
            try:
                if now - trace.stat().st_mtime < older_than_seconds:
                    continue
            except OSError:
                continue
            _add_file(
                plan.items,
                seen,
                trace,
                kind="orphan_trace",
                conversation_id=cid,
                reason="会话目录已不存在",
            )

    plan.items.sort(key=lambda item: (item.kind, item.conversation_id, str(item.path)))
    return plan


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _expected_parent(plan: CleanupPlan, item: CleanupItem) -> Path:
    if item.kind == "conversation":
        return plan.conversations_root
    if item.kind in {"snapshot", "latest_snapshot"}:
        return plan.sessions_root
    if item.kind in {"trace", "orphan_trace"}:
        return plan.traces_dir
    raise ValueError(f"未知清理类型：{item.kind}")


def _safe_to_delete(plan: CleanupPlan, item: CleanupItem) -> bool:
    """再次校验路径，防止计划被篡改后删到目录外。"""
    if item.path.is_symlink():
        return False
    if item.path.parent.resolve() != _expected_parent(plan, item).resolve():
        return False
    if item.kind == "conversation":
        return bool(CONVERSATION_ID_PATTERN.match(item.path.name))
    if item.kind == "snapshot":
        return item.path.name == f"session-{item.conversation_id}.json"
    if item.kind == "latest_snapshot":
        expected = get_session_key_latest_path(plan.workspace, item.conversation_id)
        return item.path == expected
    if item.kind == "trace":
        return item.path.name == f"{item.conversation_id}.jsonl"
    if item.kind == "orphan_trace":
        return item.path.suffix == ".jsonl"
    return False


def apply_cleanup_plan(plan: CleanupPlan) -> CleanupResult:
    """执行清理计划。失败项会记录在 ``result.failed``，不会中断其余删除。"""
    result = CleanupResult()
    for item in plan.items:
        if not _safe_to_delete(plan, item):
            result.failed.append(
                {"path": str(item.path), "error": "路径未通过安全校验，已跳过"}
            )
            continue
        try:
            if item.kind == "conversation":
                shutil.rmtree(item.path)
            else:
                item.path.unlink(missing_ok=True)
        except FileNotFoundError:
            # 计划生成后已被其它进程删掉：视作已完成，不算失败。
            continue
        except OSError as exc:
            result.failed.append({"path": str(item.path), "error": str(exc)})
            continue
        result.removed.append(item)
    return result


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------


def format_cleanup_plan(plan: CleanupPlan, *, limit: int = REPORT_LIMIT) -> str:
    lines = [
        f"工作区：{plan.workspace}",
        f"会话目录：{plan.conversations_root}",
        (
            f"策略：保留最近 {plan.keep_latest} 个会话；清理闲置超过 "
            f"{plan.older_than_seconds:g} 秒"
            f"（{plan.older_than_seconds / 86400:.1f} 天）的会话"
        ),
        f"待清理：{len(plan.items)} 项，预计释放 {format_bytes(plan.total_bytes)}",
    ]
    if not plan.items:
        lines.append("没有需要清理的内容。")
    else:
        for item in plan.items[:limit]:
            cid = f" [{item.conversation_id}]" if item.conversation_id else ""
            lines.append(
                f"- {item.kind}{cid} {item.path} "
                f"（{format_bytes(item.size)}；{item.reason}）"
            )
        if len(plan.items) > limit:
            lines.append(f"... 还有 {len(plan.items) - limit} 项未列出")
    if plan.skipped:
        lines.append(f"跳过：{len(plan.skipped)} 项（非会话目录 / 符号链接）")
    lines.append("这是 dry-run，不会删除任何文件；确认后加 --apply 执行。")
    return "\n".join(lines)


def format_cleanup_result(result: CleanupResult) -> str:
    lines = [
        f"已清理：{len(result.removed)} 项，释放 {format_bytes(result.freed_bytes)}"
    ]
    if result.failed:
        lines.append(f"失败：{len(result.failed)} 项")
        for item in result.failed[:REPORT_LIMIT]:
            lines.append(f"- {item['path']}：{item['error']}")
    return "\n".join(lines)


__all__ = [
    "CleanupItem",
    "CleanupPlan",
    "CleanupResult",
    "apply_cleanup_plan",
    "build_cleanup_plan",
    "format_cleanup_plan",
    "format_cleanup_result",
]

"""每轮文件变更快照与结果文件筛选。

qaapp 的会话目录是 Agent 的「工作台」：脚本写在 ``scripts/``、中间文件在
``tmp/``、交付物在 ``outputs/``。前端需要把「中间过程文件」和「结果文件」
直接展示出来，而不是让用户去右侧抽屉里翻。

本模块只做纯计算，不碰 HTTP 与 SSE：

1. :func:`snapshot_workspace` —— 轮次开始时记录会话目录的文件状态（大小、
   mtime、以及小文本文件的内容，用于算行数）。
2. :func:`compute_turn_files` —— 轮次结束时重新扫描并 diff，得到本轮新增/
   修改的文件；删除不记录（脚本清理临时文件不应刷屏）。
3. :func:`select_result_files` —— 从变更里挑出 ``outputs/`` 下的交付物。
4. :func:`build_payload` —— 组装 ``turn_files`` SSE 事件的 payload。

排除 ``uploads/`` 与 ``extracted/``：前者是用户上传的原始文件，后者是附件解析
产物，都不是 Agent 的编辑动作；``meta.json`` / ``attachments.json`` 属于索引
文件，同理不展示。
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 不参与「已编辑文件」统计的目录（任意层级）。
EXCLUDED_DIRS = frozenset({"uploads", "extracted", "__pycache__", ".git", ".cache"})

#: 不参与统计的索引/系统文件。
EXCLUDED_NAMES = frozenset({"meta.json", "attachments.json", ".DS_Store"})

#: 不参与统计的后缀（字节码等）。
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})

#: 单轮扫描的文件上限：避免会话目录异常膨胀时拖慢轮次收尾。
MAX_SCAN_FILES = 500
#: 只有不超过该大小的文本文件才读内容、算行数。
MAX_TEXT_BYTES = 512 * 1024
#: 一轮内保存文本内容的总预算（字符数），防止大目录占满内存。
MAX_TOTAL_TEXT_CHARS = 8 * 1024 * 1024
#: ``turn_files.changed`` 最多下发多少条。
MAX_CHANGED_FILES = 100
#: 结果文件卡片最多展示多少个。
MAX_RESULT_FILES = 8

#: ``outputs/`` 下算作「正式交付物」的格式（有这类文件时不再展示 md 草稿）。
FORMAL_RESULT_SUFFIXES = frozenset({".docx", ".pdf", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"})
#: 没有正式交付物时，md/txt 也算结果（例如纯 Markdown 方案文档）。
TEXT_RESULT_SUFFIXES = frozenset({".md", ".txt"})
#: 文档类结果（正式交付 + 文本），保持对外的稳定名称。
DOC_RESULT_SUFFIXES = FORMAL_RESULT_SUFFIXES | TEXT_RESULT_SUFFIXES
#: 没有文档类结果时，退化为这些可下载的资产（图片 / 数据 / 压缩包）。
ASSET_RESULT_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".csv", ".json", ".zip"}
)

#: 结果卡片排序：越重要的格式越靠前。
_RESULT_SUFFIX_ORDER = {
    ".docx": 0,
    ".pdf": 1,
    ".pptx": 2,
    ".xlsx": 3,
    ".md": 4,
    ".txt": 5,
    ".doc": 6,
    ".ppt": 7,
    ".xls": 8,
}

#: 没有文档类交付物时，资产类内部也按「图片 → 数据/压缩包」排序。
_ASSET_SUFFIX_ORDER = {
    ".png": 0,
    ".jpg": 1,
    ".jpeg": 2,
    ".webp": 3,
    ".gif": 4,
    ".svg": 5,
    ".csv": 6,
    ".json": 7,
    ".zip": 8,
}

#: 这些类型不读取文本内容（zip/PDF/图片字节流里没有有意义的「行数」）。
_BINARY_KINDS = frozenset({"word", "excel", "pptx", "pdf", "image", "archive"})

#: 扩展名 → 前端图标分类。命名与 ``frontend/webapp/src/types.ts`` 保持一致。
_SUFFIX_KIND = {
    ".docx": "word",
    ".doc": "word",
    ".xlsx": "excel",
    ".xls": "excel",
    ".pptx": "pptx",
    ".ppt": "pptx",
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".svg": "image",
    ".py": "script",
    ".js": "script",
    ".mjs": "script",
    ".cjs": "script",
    ".sh": "script",
    ".csv": "data",
    ".json": "data",
    ".yaml": "data",
    ".yml": "data",
    ".xml": "data",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".7z": "archive",
    ".txt": "text",
    ".log": "text",
}


@dataclass(frozen=True)
class FileState:
    """轮次开始时一个文件的状态。"""

    size: int
    mtime: float
    mtime_ns: int
    #: 文本内容；二进制、超大文件或超出总预算时为 None。
    text: str | None


@dataclass(frozen=True)
class Snapshot:
    """轮次开始时的会话目录快照。"""

    files: dict[str, FileState]
    #: 扫描是否因为文件数达到上限而截断。
    truncated: bool


@dataclass(frozen=True)
class ChangedFile:
    """本轮新增或修改的一个文件。"""

    path: str
    dir: str
    action: str  # created | modified
    size: int
    mtime: float
    added: int | None
    removed: int | None
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "dir": self.dir,
            "action": self.action,
            "size": self.size,
            "mtime": self.mtime,
            "added": self.added,
            "removed": self.removed,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ResultFile:
    """``outputs/`` 下的一份交付物。"""

    path: str
    name: str
    title: str
    kind: str
    size: int
    mtime: float
    download_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "title": self.title,
            "kind": self.kind,
            "size": self.size,
            "mtime": self.mtime,
            "download_path": self.download_path,
        }


def classify_kind(path: str) -> str:
    """按扩展名给出前端图标分类。"""
    suffix = Path(path).suffix.lower()
    return _SUFFIX_KIND.get(suffix, "other")


def snapshot_workspace(
    root: str | Path,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_text_bytes: int = MAX_TEXT_BYTES,
    max_total_text_chars: int = MAX_TOTAL_TEXT_CHARS,
) -> Snapshot:
    """记录会话目录当前的文件状态。"""
    root_path = Path(root)
    if not root_path.is_dir():
        return Snapshot(files={}, truncated=False)

    entries, truncated = _scan(root_path, max_files=max_files)
    states: dict[str, FileState] = {}
    remaining_chars = max_total_text_chars
    for rel, path, stat in entries:
        text: str | None = None
        if (
            stat.st_size <= max_text_bytes
            and remaining_chars > 0
            and classify_kind(rel) not in _BINARY_KINDS
        ):
            text = _read_text(path, max_text_bytes)
            if text is not None:
                remaining_chars -= len(text)
        states[rel] = FileState(
            size=stat.st_size,
            mtime=stat.st_mtime,
            mtime_ns=stat.st_mtime_ns,
            text=text,
        )
    return Snapshot(files=states, truncated=truncated)


def compute_turn_files(
    root: str | Path,
    before: Snapshot,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_text_bytes: int = MAX_TEXT_BYTES,
    max_total_text_chars: int = MAX_TOTAL_TEXT_CHARS,
) -> tuple[list[ChangedFile], bool]:
    """对比轮次前后的快照，返回 (本轮新增/修改的文件, 是否截断)。"""
    after = snapshot_workspace(
        root,
        max_files=max_files,
        max_text_bytes=max_text_bytes,
        max_total_text_chars=max_total_text_chars,
    )
    changed: list[ChangedFile] = []
    for rel, state in after.files.items():
        old = before.files.get(rel)
        if old is None:
            added = _line_count(state.text)
            removed = 0 if state.text is not None else None
            action = "created"
        elif state.mtime_ns == old.mtime_ns and state.size == old.size:
            continue
        else:
            added, removed = _diff_lines(old.text, state.text)
            action = "modified"
        changed.append(
            ChangedFile(
                path=rel,
                dir=_top_dir(rel),
                action=action,
                size=state.size,
                mtime=state.mtime,
                added=added,
                removed=removed,
                kind=classify_kind(rel),
            )
        )
    # 按修改时间升序：与用户观察到的编辑顺序一致。
    changed.sort(key=lambda item: (item.mtime, item.path))
    return changed, after.truncated or before.truncated


def select_result_files(
    changed: list[ChangedFile],
    *,
    conversation_id: str = "",
) -> list[ResultFile]:
    """从 ``outputs/`` 的变更里挑出结果文件。

    规则：
    - 只看本轮新建/修改、且位于 ``outputs/`` 下的文件；
    - 有正式交付物（docx/pdf/xlsx/pptx…）时只返回正式交付物，避免 md 草稿与
      报告插图抢占；
    - 没有正式交付物时返回 md/txt；再没有才退化为图片/数据/压缩包；
    - 按格式重要度、再按修改时间倒序，最多 ``MAX_RESULT_FILES`` 个。
    """
    candidates = [
        item for item in changed if item.dir == "outputs" and item.action in {"created", "modified"}
    ]
    formal = [
        item for item in candidates if Path(item.path).suffix.lower() in FORMAL_RESULT_SUFFIXES
    ]
    text_docs = [
        item for item in candidates if Path(item.path).suffix.lower() in TEXT_RESULT_SUFFIXES
    ]
    assets = [item for item in candidates if Path(item.path).suffix.lower() in ASSET_RESULT_SUFFIXES]
    chosen = formal or text_docs or assets
    chosen = sorted(chosen, key=lambda item: (_result_rank(item.path), -item.mtime))
    results: list[ResultFile] = []
    for item in chosen[:MAX_RESULT_FILES]:
        name = Path(item.path).name
        results.append(
            ResultFile(
                path=item.path,
                name=name,
                title=Path(name).stem,
                kind=item.kind,
                size=item.size,
                mtime=item.mtime,
                download_path=_download_path(conversation_id, item.path),
            )
        )
    return results


def build_payload(
    conversation_id: str,
    changed: list[ChangedFile],
    results: list[ResultFile],
    truncated: bool,
) -> dict[str, Any]:
    """组装 ``turn_files`` 事件的 payload。"""
    capped = len(changed) > MAX_CHANGED_FILES
    return {
        "changed": [item.to_dict() for item in changed[:MAX_CHANGED_FILES]],
        "results": [item.to_dict() for item in results],
        "truncated": truncated or capped,
    }


def compute_turn_files_event(
    root: str | Path,
    before: Snapshot,
    conversation_id: str = "",
) -> dict[str, Any] | None:
    """一步得到事件 payload；没有文件变更时返回 None。"""
    changed, truncated = compute_turn_files(root, before)
    if not changed:
        return None
    results = select_result_files(changed, conversation_id=conversation_id)
    return build_payload(conversation_id, changed, results, truncated)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _scan(root: Path, *, max_files: int) -> tuple[list[tuple[str, Path, os.stat_result]], bool]:
    """遍历会话目录；跳过排除目录/文件，最多返回 ``max_files`` 个。"""
    entries: list[tuple[str, Path, os.stat_result]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 就地剪枝：hidden 目录与 uploads/extracted 等不进入。
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in EXCLUDED_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if name in EXCLUDED_NAMES:
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.relative_to(root).as_posix(), path, stat))
            if len(entries) >= max_files:
                truncated = True
                return entries, truncated
    return entries, truncated


def _read_text(path: Path, max_bytes: int) -> str | None:
    """读取文本；二进制 / 超限 / 解码失败返回 None。"""
    try:
        if path.stat().st_size > max_bytes:
            return None
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    if b"\x00" in data[:1024]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _line_count(text: str | None) -> int | None:
    if text is None:
        return None
    return len(text.splitlines())


def _diff_lines(old: str | None, new: str | None) -> tuple[int | None, int | None]:
    if old is None or new is None:
        return None, None
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        lineterm="",
    )
    added = 0
    removed = 0
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _result_rank(path: str) -> int:
    """结果卡片排序权重：文档类 → 图片 → 数据/压缩包。"""
    suffix = Path(path).suffix.lower()
    if suffix in _RESULT_SUFFIX_ORDER:
        return _RESULT_SUFFIX_ORDER[suffix]
    if suffix in _ASSET_SUFFIX_ORDER:
        return len(_RESULT_SUFFIX_ORDER) + _ASSET_SUFFIX_ORDER[suffix]
    return 99


def _top_dir(relative_path: str) -> str:
    head, sep, _ = relative_path.partition("/")
    return head if sep else ""


def _download_path(conversation_id: str, relative_path: str) -> str:
    if not conversation_id:
        return ""
    return f"/api/v1/conversations/{conversation_id}/files/{relative_path}"


__all__ = [
    "ASSET_RESULT_SUFFIXES",
    "DOC_RESULT_SUFFIXES",
    "MAX_CHANGED_FILES",
    "MAX_RESULT_FILES",
    "ChangedFile",
    "FileState",
    "ResultFile",
    "Snapshot",
    "build_payload",
    "classify_kind",
    "compute_turn_files",
    "compute_turn_files_event",
    "select_result_files",
    "snapshot_workspace",
]

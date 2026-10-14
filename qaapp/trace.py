"""对话执行过程轨迹存储。

在 ohmo 会话快照之外，额外落一份按轮次、带时间戳的流式事件轨迹
（``~/.ohmo/sessions/traces/{conversation_id}.jsonl``），用于满足
“查询历史对话记录，包括中间的执行过程”的需求。

每行一个 JSON 对象：

.. code-block:: json

    {"ts": 1730000000.0, "turn_id": "...", "message_id": "...",
     "event": "tool_started", "tool_name": "file_write",
     "tool_input": {}, "output": "", "is_error": false,
     "text": "", "meta": {}}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ohmo.workspace import get_sessions_dir

TRACES_DIRNAME = "traces"


def get_traces_dir(workspace: str | Path | None = None) -> Path:
    """返回轨迹目录，必要时创建。"""
    traces_dir = get_sessions_dir(workspace) / TRACES_DIRNAME
    traces_dir.mkdir(parents=True, exist_ok=True)
    return traces_dir


def get_trace_path(workspace: str | Path | None, conversation_id: str) -> Path:
    return get_traces_dir(workspace) / f"{conversation_id}.jsonl"


class ConversationTraceStore:
    """按对话追加/读取执行轨迹的轻量存储。"""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = workspace

    def append(self, conversation_id: str, event: dict[str, Any]) -> None:
        """追加一条轨迹事件（尽力而为，失败不抛出）。"""
        event = dict(event)
        event.setdefault("ts", time.time())
        event.setdefault("conversation_id", conversation_id)
        path = get_trace_path(self._workspace, conversation_id)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # 轨迹写入失败不应中断对话。
            pass

    def load_trace(
        self,
        conversation_id: str,
        *,
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取一个对话的完整轨迹；``turn_id`` 非空时仅返回该轮。"""
        path = get_trace_path(self._workspace, conversation_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if turn_id is not None and event.get("turn_id") != turn_id:
                    continue
                events.append(event)
        except OSError:
            return []
        return events

    def list_turn_ids(self, conversation_id: str) -> list[str]:
        """按首次出现顺序返回该对话的所有 turn_id。"""
        seen: list[str] = []
        for event in self.load_trace(conversation_id):
            turn_id = event.get("turn_id")
            if turn_id and turn_id not in seen:
                seen.append(turn_id)
        return seen


__all__ = [
    "ConversationTraceStore",
    "get_trace_path",
    "get_traces_dir",
]

"""Per-conversation session state and approval primitives."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING

from openharness.ui.runtime import RuntimeBundle
from qaapp.workspace.files import AttachmentStore, ConversationPaths

if TYPE_CHECKING:
    from qaapp.server.service import QaWebService

def _edit_key(path: str) -> str:
    """本轮内「同一文件已批准编辑」的记忆键。"""
    return f"edit:{path}"



_PERMISSION_MODE_ALIASES = {
    "bypasspermissions": "full_auto",
    "dontask": "full_auto",
    "auto": "full_auto",
    "full_auto": "full_auto",
    "plan": "plan",
    "default": "default",
}


def _normalize_permission_mode(value: str | None) -> str:
    """把 AgentDefinition 风格的权限值映射为 openharness PermissionMode。"""
    if not value:
        return "default"
    return _PERMISSION_MODE_ALIASES.get(value.strip().lower(), "default")


class PendingRequest:
    """一个等待前端/API 响应的审批/提问。"""

    def __init__(
        self, request_id: str, kind: str, payload: dict, created_at: float | None = None
    ) -> None:
        self.request_id = request_id
        self.kind = kind  # permission | question | edit
        self.payload = payload
        #: 请求产生时间（epoch 秒），供跨会话的待审批快照排序与展示。
        self.created_at = created_at if created_at is not None else time.time()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


class ConversationSession:
    """一个对话会话：运行时 bundle + 审批状态 + 订阅者 + 会话临时目录。"""

    def __init__(self, conversation_id: str, service: QaWebService) -> None:
        self.conversation_id = conversation_id
        self.service = service
        self.cwd = service.cwd
        self.bundle: RuntimeBundle | None = None
        self.busy = False
        #: 本轮开始时间（epoch 秒）；``busy`` 为 False 时为 None。
        self.busy_since: float | None = None
        self.current_task: asyncio.Task | None = None
        self.current_turn: dict | None = None
        self.subscribers: list[asyncio.Queue] = []
        self.pending: dict[str, PendingRequest] = {}
        self.last_tool_inputs: dict[str, dict] = {}
        self.always_approved: set[str] = set()
        # 本轮（turn）内已批准过的工具/文件：避免同一任务里对同一工具反复弹窗。
        # 每轮开始清空，因此新一轮任务仍会重新确认一次。
        self.turn_approved: set[str] = set()
        # 已处理过的审批 id（有界），用于把重复提交做成幂等，避免误报 404 红条。
        self.resolved_approvals: deque[str] = deque(maxlen=200)
        self.last_error: str | None = None
        #: 本会话的临时目录（uploads/extracted/scripts/tmp/outputs）。
        self.paths = ConversationPaths.for_conversation(
            service._workspace, conversation_id, service.webapp.conversation_workspace
        )
        #: 附件索引（attachments.json）。
        self.attachments = AttachmentStore(self.paths)
        #: 本轮携带的附件记录（供系统提示与外部 Agent 使用）。
        self.current_attachments: list[dict] = []
        #: bundle 创建时的默认系统提示（agent 自带提示时以 agent 的为准）。
        self.system_prompt_base: str | None = None

    def emit(self, event: dict) -> None:
        """向所有订阅者推送一个事件（非阻塞，尽力而为）。"""
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

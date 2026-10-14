"""qaapp 对话 API 的请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, field_validator


class SendMessageRequest(BaseModel):
    """发消息请求体。"""

    content: str
    agent: str | None = None  # 显式指定 agent，覆盖路由
    stream: bool = True  # True=SSE 流式返回；False=同步返回 turn_id

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class ApprovalRequest(BaseModel):
    """审批响应体。"""

    decision: Literal["approve", "deny", "always"]
    reply: str | None = None  # question / edit 审批时的补充回答


class AgentInfo(BaseModel):
    """Agent 摘要。"""

    id: str
    kind: str
    name: str
    description: str
    enabled: bool


class ConversationSummary(BaseModel):
    """会话摘要。"""

    conversation_id: str
    summary: str
    message_count: int
    model: str
    created_at: float


class TurnTraceResponse(BaseModel):
    """单轮执行轨迹。"""

    conversation_id: str
    turn_id: str
    events: list[dict[str, Any]]


class ConversationDetail(BaseModel):
    """会话详情：消息 + 轨迹。"""

    conversation_id: str
    summary: str
    model: str
    created_at: float
    messages: list[dict[str, Any]]
    turns: list[dict[str, Any]]  # 每轮一个 {turn_id, message_id, events}
    pending_approvals: list[dict[str, Any]]


def error_detail(status: int, code: str, message: str) -> dict[str, Any]:
    """统一错误响应体。"""
    return {"status": status, "code": code, "message": message}


__all__ = [
    "AgentInfo",
    "ApprovalRequest",
    "ConversationDetail",
    "ConversationSummary",
    "SendMessageRequest",
    "TurnTraceResponse",
    "error_detail",
]

"""qaapp 对话 API 的请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SendMessageRequest(BaseModel):
    """发消息请求体。

    ``content`` 可以为空——只要带了附件（``attachment_ids``），用户完全可以直接
    「把这个表分析一下」而不输入任何文字。
    """

    content: str = ""
    agent: str | None = None  # 显式指定 agent，覆盖路由
    stream: bool = True  # True=SSE 流式返回；False=同步返回 turn_id
    attachment_ids: list[str] = Field(default_factory=list)  # 本轮的附件（最多 2 个）

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        return value or ""

    @field_validator("attachment_ids")
    @classmethod
    def _dedupe_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value or []:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _require_payload(self) -> SendMessageRequest:
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("content 与 attachment_ids 至少提供一个")
        return self


class ApprovalRequest(BaseModel):
    """审批响应体。"""

    decision: Literal["approve", "deny", "always"]
    reply: str | None = None  # question 的选项 label / 自定义回答，或 edit 的补充说明


class AgentInfo(BaseModel):
    """Agent 摘要。"""

    id: str
    kind: str
    name: str
    description: str
    enabled: bool


class AttachmentInfo(BaseModel):
    """一个上传附件的元数据（对外字段，路径均为会话目录内的相对路径）。"""

    attachment_id: str
    name: str
    kind: str
    size: int
    uploaded_at: float
    parse_status: str  # ok | failed
    parse_error: str = ""
    text_chars: int = 0
    preview: str = ""
    stored_path: str = ""
    extracted_path: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkspaceFile(BaseModel):
    """会话临时目录中的一个文件。"""

    path: str  # 相对会话目录，如 uploads/报告.docx
    dir: str  # uploads | extracted | scripts | tmp | outputs
    size: int
    mtime: float


class ConversationWorkspaceResponse(BaseModel):
    """会话临时目录清单。"""

    conversation_id: str
    dir: str
    files: list[WorkspaceFile] = Field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False


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
    "AttachmentInfo",
    "ConversationDetail",
    "ConversationSummary",
    "ConversationWorkspaceResponse",
    "SendMessageRequest",
    "TurnTraceResponse",
    "WorkspaceFile",
    "error_detail",
]

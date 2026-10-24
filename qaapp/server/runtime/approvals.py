"""Approval / question request lifecycle."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from qaapp.server.session import ConversationSession, PendingRequest, _edit_key


def public_pending_payload(payload: dict) -> dict:
    """跨会话待审批快照用的载荷。

    ``payload.input`` 是工具入参原文（例如整篇报告 JSON，可能几十 KB），
    轮询快照每几秒下发一次，必须裁掉；完整的入参仍然通过 SSE 事件和轨迹下发，
    前端在增量跟进时会用完整载荷覆盖卡片。
    """
    return {key: value for key, value in payload.items() if key != "input"}


class ApprovalsMixin:
    async def _request_approval(
        self, session: ConversationSession, kind: str, payload: dict
    ) -> object:
        request_id = uuid4().hex[:12]
        request = PendingRequest(request_id, kind, payload)
        session.pending[request_id] = request
        # 走统一事件通道：日志、轨迹、SSE 广播一次完成。
        # 之前直接 session.emit + trace.append，导致审批请求既没有日志、
        # 也不受统一的生命周期管理，出问题时只能靠猜。
        self._emit(
            session,
            "approval_required",
            {"request_id": request_id, "kind": kind, "payload": payload},
        )

        timeout = self.dangerous.approval_timeout_seconds
        try:
            return await asyncio.wait_for(asyncio.shield(request.future), timeout=timeout)
        except TimeoutError:
            session.pending.pop(request_id, None)
            timeout_text = (
                f"提问超时（{timeout}s），已跳过本次确认"
                if kind == "question"
                else f"审批超时（{timeout}s），已按拒绝处理"
            )
            self._emit(session, "status", {"text": timeout_text})
            self._emit(
                session,
                "approval_resolved",
                {
                    "request_id": request_id,
                    "kind": kind,
                    "decision": "timeout",
                    "reply": "",
                },
            )
            return "" if kind == "question" else ("reject" if kind == "edit" else False)

    def resolve_approval(
        self, session: ConversationSession, request_id: str, decision: str, reply: str | None
    ) -> None:
        request = session.pending.pop(request_id, None)
        if request is None:
            if request_id in session.resolved_approvals:
                return
            raise KeyError(request_id)
        session.resolved_approvals.append(request_id)

        approved = decision in {"approve", "always"}
        if request.kind == "permission":
            tool = str(request.payload.get("tool", ""))
            if decision == "always":
                session.always_approved.add(tool)
            if approved and tool:
                session.turn_approved.add(tool)
            request.future.set_result(approved)
        elif request.kind == "edit":
            if decision == "deny":
                request.future.set_result("reject")
            else:
                session.turn_approved.add(_edit_key(str(request.payload.get("path", ""))))
                request.future.set_result("always" if decision == "always" else "once")
        else:  # question
            request.future.set_result(reply or "")

        self._emit(
            session,
            "approval_resolved",
            {
                "request_id": request_id,
                "kind": request.kind,
                "decision": decision,
                "reply": reply or "",
            },
        )

"""Approval / question request lifecycle."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from qaapp.server.session import ConversationSession, PendingRequest, _edit_key


class ApprovalsMixin:
    async def _request_approval(
        self, session: ConversationSession, kind: str, payload: dict
    ) -> object:
        request_id = uuid4().hex[:12]
        request = PendingRequest(request_id, kind, payload)
        session.pending[request_id] = request
        event = self._event(
            session,
            "approval_required",
            {"request_id": request_id, "kind": kind, "payload": payload},
        )
        session.emit(event)
        self.trace_store.append(session.conversation_id, event)

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

"""SSE event construction and fan-out."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from qaapp.server.session import ConversationSession

logger = logging.getLogger(__name__)


class EventsMixin:
    def _event(self, session: ConversationSession, type_: str, data: dict | None = None) -> dict:
        turn = session.current_turn or {}
        return {
            "type": type_,
            "conversation_id": session.conversation_id,
            "turn_id": turn.get("turn_id"),
            "message_id": turn.get("message_id"),
            "ts": time.time(),
            **(data or {}),
        }

    def _emit(self, session: ConversationSession, type_: str, data: dict | None = None) -> dict:
        event = self._event(session, type_, data)
        detail = ""
        if data:
            if data.get("tool") or data.get("tool_name"):
                detail += f" tool={data.get('tool') or data.get('tool_name')}"
            if data.get("request_id"):
                detail += f" request={data.get('request_id')}"
            if data.get("kind"):
                detail += f" kind={data.get('kind')}"
        logger.info(
            "sse emit: type=%s conversation=%s turn=%s text_len=%d%s",
            type_,
            session.conversation_id,
            event.get("turn_id"),
            len(data.get("text") or "") if data else 0,
            detail,
        )
        session.emit(event)
        self.trace_store.append(session.conversation_id, event)
        return event


async def sse_turn_stream(session: ConversationSession, queue: asyncio.Queue):
    """把一个订阅队列变成 SSE 事件流。

    只负责「订阅 → 转发 → 退订」这一件事：客户端刷新 / 切走导致连接断开时，
    这里绝不能顺手把 ``session.busy`` / ``current_task`` 清掉——后台的回合任务
    还在跑，清了会让会话看起来空闲（审批、中断、并发发送都会出错）。
    回合状态的收尾统一由 ``run_turn`` 的 ``finally`` 负责。
    """

    try:
        while True:
            event = await queue.get()
            yield event
            if event.get("type") == "turn_complete":
                break
    finally:
        with contextlib.suppress(ValueError):
            session.subscribers.remove(queue)


__all__ = ["EventsMixin", "sse_turn_stream"]

"""SSE event construction and fan-out."""

from __future__ import annotations

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
        logger.info(
            "sse emit: type=%s conversation=%s turn=%s text_len=%d",
            type_,
            session.conversation_id,
            event.get("turn_id"),
            len(data.get("text") or "") if data else 0,
        )
        session.emit(event)
        self.trace_store.append(session.conversation_id, event)
        return event

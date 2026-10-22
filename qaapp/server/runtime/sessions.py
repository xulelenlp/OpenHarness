"""Session pool and snapshot persistence."""

from __future__ import annotations

from pathlib import Path

from qaapp.server.session import ConversationSession


class SessionsMixin:
    async def get_or_create_session(
        self, conversation_id: str, *, create: bool = False
    ) -> ConversationSession:
        async with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None:
                if not create:
                    raise KeyError(conversation_id)
                session = ConversationSession(conversation_id, self)
                self._sessions[conversation_id] = session
            # 幂等保证会话临时目录存在（会话可能是本次进程重启后首次被访问）。
            session.paths.ensure()
            return session

    async def _save_snapshot(self, session: ConversationSession) -> None:
        bundle = session.bundle
        if bundle is None:
            return
        self.session_backend.save_snapshot(
            cwd=Path(session.cwd),
            model=bundle.engine.model,
            system_prompt=bundle.engine.system_prompt,
            messages=bundle.engine.messages,
            usage=bundle.engine.total_usage,
            session_id=session.conversation_id,
            session_key=session.conversation_id,
            tool_metadata=bundle.engine.tool_metadata,
        )

    def _persist_empty_conversation(self, conversation_id: str) -> None:
        """创建对话时落一个空快照，保证 list/detail 立即可见。"""
        from openharness.api.usage import UsageSnapshot

        self.session_backend.save_snapshot(
            cwd=Path(self.cwd),
            model="",
            system_prompt="",
            messages=[],
            usage=UsageSnapshot(),
            session_id=conversation_id,
            session_key=conversation_id,
        )

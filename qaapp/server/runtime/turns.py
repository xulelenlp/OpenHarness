"""One conversation turn: attachments, agent routing and completion."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from uuid import uuid4

from qaapp.agents.router import AgentNotFoundError, NoDefaultAgentError, resolve_agent
from qaapp.config import KIND_EXTERNAL
from qaapp.server.constants import DEFAULT_ATTACHMENT_PROMPT
from qaapp.server.session import ConversationSession
from qaapp.workspace.files import AttachmentNotFound, public_record
from qaapp.workspace.turns import Snapshot, compute_turn_files_event, snapshot_workspace

logger = logging.getLogger(__name__)


class TurnsMixin:
    async def run_turn(
        self,
        session: ConversationSession,
        content: str,
        agent_override: str | None,
        attachment_ids: list[str] | None = None,
    ) -> None:
        session.current_turn = {
            "turn_id": uuid4().hex[:12],
            "message_id": uuid4().hex[:12],
        }
        session.turn_approved.clear()
        session.current_attachments = []
        # 回合状态由任务本身负责：SSE 连接只是观察者，断开不代表回合结束。
        session.busy = True
        session.busy_since = time.time()
        session.current_task = asyncio.current_task()
        before_files: Snapshot | None = None
        try:
            before_files = await self._snapshot_turn_files(session)
            records = self.resolve_attachments(session, attachment_ids or [])
            session.current_attachments = records
            self._emit(
                session,
                "attachments_ready",
                {"attachments": [public_record(record) for record in records]},
            )
            content = content.strip() or (DEFAULT_ATTACHMENT_PROMPT if records else "")
            resolved = await resolve_agent(
                content,
                registry=self.registry,
                explicit_agent=agent_override,
            )
            agent = resolved.agent
            self._emit(
                session,
                "agent_routed",
                {"agent": agent.id, "kind": agent.kind, "route": resolved.route},
            )
            if agent.kind == KIND_EXTERNAL:
                await self._run_external(session, agent, content)
            else:
                await self._run_harness(session, agent, content)
        except AttachmentNotFound as exc:
            self._emit(session, "error", {"text": str(exc), "code": "attachment_not_found"})
            raise
        except AgentNotFoundError as exc:
            self._emit(session, "error", {"text": str(exc), "code": "agent_not_found"})
            raise
        except NoDefaultAgentError as exc:
            self._emit(session, "error", {"text": str(exc), "code": "no_default_agent"})
            raise
        except asyncio.CancelledError:
            self._emit(session, "status", {"text": "已中断当前回答"})
            raise
        except SystemExit as exc:
            # 模型鉴权等底层组件在 CLI 语义下用 SystemExit 退出；Web 服务里必须
            # 把它降级成一次失败的回答，否则单个请求会终止整个 uvicorn 进程。
            cause = exc.__context__ or exc
            detail = str(cause).strip() or "运行环境提前退出（SystemExit）"
            logger.exception("turn aborted by SystemExit conversation=%s", session.conversation_id)
            session.last_error = detail
            self._emit(
                session,
                "error",
                {"text": f"处理失败：{detail}", "code": "runtime_exit"},
            )
        except Exception as exc:
            logger.exception("turn failed conversation=%s", session.conversation_id)
            session.last_error = str(exc)
            self._emit(session, "error", {"text": f"处理失败: {exc}"})
        finally:
            try:
                await self._save_snapshot(session)
            except Exception:
                logger.exception("save snapshot failed for %s", session.conversation_id)
            with contextlib.suppress(OSError):
                session.paths.touch_turn()
            if before_files is not None:
                self._emit_turn_files(session, before_files)
            self._emit(session, "turn_complete", {})
            session.current_turn = None
            session.busy = False
            session.busy_since = None
            # 只有当前任务仍是本任务时才清空，避免把后来者的指针抹掉。
            if session.current_task is asyncio.current_task():
                session.current_task = None

    async def _snapshot_turn_files(self, session: ConversationSession) -> Snapshot | None:
        try:
            return await asyncio.to_thread(snapshot_workspace, session.paths.root)
        except Exception:
            logger.exception("snapshot turn files failed for %s", session.conversation_id)
            return None

    def _emit_turn_files(self, session: ConversationSession, before: Snapshot) -> None:
        """把本轮新增/修改的文件作为 ``turn_files`` 事件下发。"""
        try:
            payload = compute_turn_files_event(
                session.paths.root,
                before,
                session.conversation_id,
            )
        except Exception:
            logger.exception("compute turn files failed for %s", session.conversation_id)
            return
        if payload is None:
            return
        self._emit(session, "turn_files", payload)

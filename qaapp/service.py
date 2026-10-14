"""qaapp Web 服务：对话 API（REST + SSE + WebSocket）。

基于 Starlette + uvicorn + sse-starlette，复用 OpenHarness 的 ``build_runtime``
运行时与 ohmo 的会话/轨迹存储。单个 gateway 进程内维护“每对话一个
RuntimeBundle”的会话池。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from ohmo.session_storage import OhmoSessionBackend
from ohmo.workspace import get_workspace_root
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import (
    AssistantReasoningDelta,
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionProgress,
    ToolExecutionStarted,
)
from openharness.permissions.modes import PermissionMode
from openharness.tools.base import ToolExecutionContext
from openharness.ui.runtime import RuntimeBundle, build_runtime, close_runtime, start_runtime
from qaapp.agent_router import AgentNotFoundError, NoDefaultAgentError, resolve_agent
from qaapp.config import (
    KIND_EXTERNAL,
    KIND_SUBAGENT,
    AgentEntry,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
)
from qaapp.dangerous import DangerousActionGate, DangerousPermissionChecker
from qaapp.trace import ConversationTraceStore

logger = logging.getLogger(__name__)


def _edit_key(path: str) -> str:
    """本轮内「同一文件已批准编辑」的记忆键。"""
    return f"edit:{path}"


# 仓库根（qaapp 位于仓库根下），用于让仓库自带的 plugins 目录可被发现。
_REPO_ROOT = Path(__file__).resolve().parents[1]

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

    def __init__(self, request_id: str, kind: str, payload: dict) -> None:
        self.request_id = request_id
        self.kind = kind  # permission | question | edit
        self.payload = payload
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


class ConversationSession:
    """一个对话会话：运行时 bundle + 审批状态 + 订阅者。"""

    def __init__(self, conversation_id: str, service: QaWebService) -> None:
        self.conversation_id = conversation_id
        self.service = service
        self.cwd = service.cwd
        self.bundle: RuntimeBundle | None = None
        self.busy = False
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

    def emit(self, event: dict) -> None:
        """向所有订阅者推送一个事件（非阻塞，尽力而为）。"""
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


class QaWebService:
    """对话 API 服务。"""

    def __init__(
        self,
        *,
        workspace: str | Path | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self._workspace = workspace
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        # 让 openharness 的运行时数据（记忆、日志等）也落在 workspace 下，
        # 便于整体迁移、隔离与多实例部署（若不显式设置则沿用全局默认）。
        root = get_workspace_root(workspace)
        if not os.environ.get("OPENHARNESS_DATA_DIR"):
            os.environ["OPENHARNESS_DATA_DIR"] = str(root / "data")
        if not os.environ.get("OPENHARNESS_LOGS_DIR"):
            os.environ["OPENHARNESS_LOGS_DIR"] = str(root / "logs")
        self.webapp = load_webapp_config(workspace)
        self.registry = load_agent_registry(workspace)
        self.dangerous = load_dangerous_actions(workspace)
        self.gate = DangerousActionGate(self.dangerous)
        self.trace_store = ConversationTraceStore(workspace)
        self.session_backend = OhmoSessionBackend(workspace)
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = asyncio.Lock()
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

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
            return session

    async def ensure_bundle(self, session: ConversationSession) -> RuntimeBundle:
        """懒构建/恢复一个会话的运行时 bundle。"""
        if session.bundle is not None:
            return session.bundle

        snapshot = self.session_backend.load_by_id(Path(session.cwd), session.conversation_id)
        restore_messages = snapshot.get("messages") if snapshot else None
        restore_tool_metadata = snapshot.get("tool_metadata") if snapshot else None

        async def _permission_prompt(tool_name: str, reason: str) -> bool:
            # 本会话「总是允许」、或本轮已批准过该工具 → 直接放行。
            # 「开发脚本 + git 提交」这类任务会连续多次调用同一个工具，
            # 若每次都弹窗会严重骚扰用户，故在本轮内记忆一次批准。
            if tool_name in session.always_approved or tool_name in session.turn_approved:
                return True
            payload = {
                "tool": tool_name,
                "reason": reason,
                "input": session.last_tool_inputs.get(tool_name),
            }
            value = await self._request_approval(session, "permission", payload)
            return bool(value)

        async def _ask_user_prompt(question: str) -> str:
            value = await self._request_approval(session, "question", {"question": question})
            return str(value or "")

        async def _edit_approval_prompt(path: str, diff: str, added: int, removed: int) -> str:
            # 本轮已批准过该文件 → 不再重复确认（同一文件多轮编辑很常见）。
            if _edit_key(path) in session.turn_approved:
                return "once"
            value = await self._request_approval(
                session, "edit", {"path": path, "diff": diff, "added": added, "removed": removed}
            )
            return value if value in {"once", "always"} else "reject"

        kwargs: dict = {
            "cwd": session.cwd,
            "permission_prompt": _permission_prompt,
            "ask_user_prompt": _ask_user_prompt,
            "edit_approval_prompt": _edit_approval_prompt,
            "restore_messages": restore_messages,
            "restore_tool_metadata": restore_tool_metadata,
            "session_backend": self.session_backend,
            "extra_plugin_roots": (str(_REPO_ROOT / "plugins"),),
        }
        if self.webapp.provider_profile:
            kwargs["active_profile"] = self.webapp.provider_profile

        bundle = await build_runtime(**kwargs)
        await start_runtime(bundle)
        session.bundle = bundle
        return bundle

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

    # ------------------------------------------------------------------
    # 事件与轨迹
    # ------------------------------------------------------------------

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
        # 观测日志：记录每个 SSE 事件，便于排查"前端只有思考中…"时定位事件断在哪一环。
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

    # ------------------------------------------------------------------
    # 审批
    # ------------------------------------------------------------------

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
            self._emit(
                session,
                "status",
                {"text": f"审批超时（{timeout}s），已按拒绝处理"},
            )
            return "" if kind == "question" else ("reject" if kind == "edit" else False)

    def resolve_approval(
        self, session: ConversationSession, request_id: str, decision: str, reply: str | None
    ) -> None:
        request = session.pending.pop(request_id, None)
        if request is None:
            # 重复提交（用户连点两次 / 前端重试）：该审批已是终态则幂等成功，
            # 不再抛 404 让前端弹「审批请求不存在或已过期」红条。
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
                # 本轮内同一工具不再重复确认（下一轮任务仍会重新确认一次）
                session.turn_approved.add(tool)
            request.future.set_result(approved)
        elif request.kind == "edit":
            if decision == "deny":
                request.future.set_result("reject")
            else:
                # once / always 都在本轮内记忆该文件，避免同一文件被反复确认。
                session.turn_approved.add(_edit_key(str(request.payload.get("path", ""))))
                request.future.set_result("always" if decision == "always" else "once")
        else:  # question
            request.future.set_result(reply or "")

    # ------------------------------------------------------------------
    # 轮次处理
    # ------------------------------------------------------------------

    async def run_turn(
        self,
        session: ConversationSession,
        content: str,
        agent_override: str | None,
    ) -> None:
        session.current_turn = {
            "turn_id": uuid4().hex[:12],
            "message_id": uuid4().hex[:12],
        }
        # 新一轮对话开始：重置本轮审批记忆，保证新任务里同工具/同文件仍会确认一次。
        session.turn_approved.clear()
        try:
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
        except AgentNotFoundError as exc:
            self._emit(session, "error", {"text": str(exc), "code": "agent_not_found"})
            raise
        except NoDefaultAgentError as exc:
            self._emit(session, "error", {"text": str(exc), "code": "no_default_agent"})
            raise
        except asyncio.CancelledError:
            self._emit(session, "status", {"text": "已中断当前回答"})
            raise
        except Exception as exc:
            logger.exception("turn failed conversation=%s", session.conversation_id)
            session.last_error = str(exc)
            self._emit(session, "error", {"text": f"处理失败: {exc}"})
        finally:
            try:
                await self._save_snapshot(session)
            except Exception:
                logger.exception("save snapshot failed for %s", session.conversation_id)
            self._emit(session, "turn_complete", {})
            session.current_turn = None

    async def _run_harness(self, session: ConversationSession, agent: AgentEntry, message: str) -> None:
        bundle = await self.ensure_bundle(session)
        engine = bundle.engine

        system_prompt = self._resolve_system_prompt(agent)
        if system_prompt:
            engine.set_system_prompt(system_prompt)
        if agent.model:
            engine.set_model(agent.model)
        if agent.max_turns:
            engine.set_max_turns(agent.max_turns)

        # 权限：settings.json 基础权限 + agent 权限模式 + 危险操作门禁。
        base_permission: PermissionSettings = engine._permission_checker._settings
        mode = PermissionMode(_normalize_permission_mode(agent.permission_mode))
        checker = DangerousPermissionChecker(
            base_permission.model_copy(update={"mode": mode}), self.gate
        )
        engine.set_permission_checker(checker)

        async for event in engine.submit_message(message):
            await self._handle_stream_event(session, event)

    async def _run_external(self, session: ConversationSession, agent: AgentEntry, message: str) -> None:
        bundle = await self.ensure_bundle(session)
        tool = bundle.tool_registry.get("external_agent")
        if tool is None:
            self._emit(session, "error", {"text": "external_agent 工具未加载（插件缺失）"})
            return

        async def _stream(text: str) -> None:
            if text:
                self._emit(session, "assistant_delta", {"text": text})

        arguments = tool.input_model(
            agent=agent.external_ref, message=message, conversation_id=session.conversation_id
        )
        context = ToolExecutionContext(cwd=Path(session.cwd), stream_text=_stream)
        result = await tool.execute(arguments, context)

        if result.is_error:
            self._emit(session, "error", {"text": result.output})
        else:
            self._emit(session, "assistant_complete", {"text": result.output})
            # 外部 Agent 的结果也写入对话历史，保证历史/轨迹接口统一。
            engine = bundle.engine
            engine.load_messages(
                engine.messages
                + [
                    ConversationMessage.from_user_text(message),
                    ConversationMessage(role="assistant", content=[TextBlock(text=result.output)]),
                ]
            )

    async def _handle_stream_event(self, session: ConversationSession, event: StreamEvent) -> None:
        if isinstance(event, AssistantTextDelta):
            self._emit(session, "assistant_delta", {"text": event.text})
        elif isinstance(event, AssistantReasoningDelta):
            self._emit(session, "reasoning_delta", {"text": event.text})
        elif isinstance(event, AssistantTurnComplete):
            self._emit(session, "assistant_complete", {"text": event.message.text})
        elif isinstance(event, ToolExecutionStarted):
            session.last_tool_inputs[event.tool_name] = event.tool_input
            self._emit(session, "tool_started", {"tool_name": event.tool_name, "tool_input": event.tool_input})
        elif isinstance(event, ToolExecutionCompleted):
            self._emit(
                session,
                "tool_completed",
                {"tool_name": event.tool_name, "output": event.output, "is_error": event.is_error},
            )
        elif isinstance(event, ToolExecutionProgress):
            self._emit(session, "tool_progress", {"tool_name": event.tool_name, "text": event.text})
        elif isinstance(event, StatusEvent):
            self._emit(session, "status", {"text": event.message})
        elif isinstance(event, ErrorEvent):
            self._emit(session, "error", {"text": event.message})
        elif isinstance(event, CompactProgressEvent):
            self._emit(session, "status", {"text": event.message or event.phase})

    def _resolve_system_prompt(self, agent: AgentEntry) -> str:
        """解析 agent 的系统提示：harness 用自身配置，subagent 复用 AgentDefinition。"""
        if agent.system_prompt:
            return agent.system_prompt
        if agent.system_prompt_file:
            path = Path(agent.system_prompt_file).expanduser()
            if path.exists():
                return path.read_text(encoding="utf-8")
        if agent.kind == KIND_SUBAGENT and agent.subagent_type:
            try:
                from openharness.coordinator.agent_definitions import get_agent_definition

                definition = get_agent_definition(agent.subagent_type)
                if definition is not None and definition.system_prompt:
                    return definition.system_prompt
            except (ImportError, ValueError, OSError):
                logger.debug("加载 subagent 定义失败 type=%s", agent.subagent_type, exc_info=True)
            return f"You are acting as the '{agent.subagent_type}' subagent."
        return ""

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_conversations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        snapshots = self.session_backend.list_snapshots(Path(self.cwd), limit=limit + offset)
        return [
            {
                "conversation_id": item["session_id"],
                "summary": item["summary"],
                "message_count": item["message_count"],
                "model": item["model"],
                "created_at": item["created_at"],
            }
            for item in snapshots[offset:]
        ]

    def conversation_detail(self, conversation_id: str) -> dict | None:
        snapshot = self.session_backend.load_by_id(Path(self.cwd), conversation_id)
        if snapshot is None:
            return None
        trace = self.trace_store.load_trace(conversation_id)
        turns: list[dict] = []
        by_turn: dict[str, list[dict]] = {}
        for event in trace:
            turn_id = event.get("turn_id")
            if not turn_id:
                continue
            by_turn.setdefault(turn_id, []).append(event)
        for turn_id, events in by_turn.items():
            message_id = next((e.get("message_id") for e in events if e.get("message_id")), "")
            turns.append({"turn_id": turn_id, "message_id": message_id, "events": events})
        return {
            "conversation_id": conversation_id,
            "summary": snapshot.get("summary", ""),
            "model": snapshot.get("model", ""),
            "created_at": snapshot.get("created_at"),
            "messages": snapshot.get("messages", []),
            "turns": turns,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            if session.bundle is not None:
                try:
                    await close_runtime(session.bundle)
                except Exception:
                    logger.exception("close_runtime failed for %s", session.conversation_id)
        self._sessions.clear()

    # ------------------------------------------------------------------
    # Starlette 应用
    # ------------------------------------------------------------------

    def _build_app(self):
        from sse_starlette.sse import EventSourceResponse
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route, WebSocketRoute
        from starlette.websockets import WebSocketDisconnect

        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=self.webapp.cors_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        ]

        async def _auth(request: Request):
            tokens = self.webapp.api_tokens
            if not tokens:
                return None
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                return JSONResponse({"status": 401, "code": "unauthorized", "message": "缺少 Bearer token"}, status_code=401)
            token = header[len("Bearer "):].strip()
            if token not in tokens:
                return JSONResponse({"status": 403, "code": "forbidden", "message": "token 无效"}, status_code=403)
            return None

        # --- 路由处理 ---

        async def healthz(request: Request):
            return JSONResponse({"status": "ok", "active_sessions": len(self._sessions)})

        async def list_agents(request: Request):
            return JSONResponse(
                {
                    "agents": [
                        {
                            "id": a.id,
                            "kind": a.kind,
                            "name": a.name,
                            "description": a.description,
                            "enabled": a.enabled,
                        }
                        for a in self.registry.agents
                    ]
                }
            )

        async def create_conversation(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = uuid4().hex[:12]
            await self.get_or_create_session(conversation_id, create=True)
            self._persist_empty_conversation(conversation_id)
            return JSONResponse({"conversation_id": conversation_id}, status_code=201)

        async def list_conversations_route(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            limit = int(request.query_params.get("limit", "50"))
            offset = int(request.query_params.get("offset", "0"))
            return JSONResponse({"conversations": self.list_conversations(limit, offset)})

        async def get_conversation(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = request.path_params["conversation_id"]
            detail = self.conversation_detail(conversation_id)
            if detail is None:
                return JSONResponse({"status": 404, "code": "not_found", "message": "对话不存在"}, status_code=404)
            session = self._sessions.get(conversation_id)
            detail["pending_approvals"] = [
                {"request_id": r.request_id, "kind": r.kind, "payload": r.payload}
                for r in (session.pending.values() if session else [])
            ]
            return JSONResponse(detail)

        async def get_turn(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = request.path_params["conversation_id"]
            turn_id = request.path_params["turn_id"]
            events = self.trace_store.load_trace(conversation_id, turn_id=turn_id)
            if not events:
                return JSONResponse({"status": 404, "code": "not_found", "message": "轮次不存在"}, status_code=404)
            return JSONResponse({"conversation_id": conversation_id, "turn_id": turn_id, "events": events})

        async def post_message(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = request.path_params["conversation_id"]
            try:
                body = await request.json()
            except (ValueError, TypeError):
                return JSONResponse({"status": 400, "code": "bad_request", "message": "请求体不是合法 JSON"}, status_code=400)
            try:
                from qaapp.schemas import SendMessageRequest

                payload = SendMessageRequest.model_validate(body)
            except ValueError as exc:
                return JSONResponse({"status": 422, "code": "unprocessable", "message": str(exc)}, status_code=422)

            session = await self.get_or_create_session(conversation_id, create=True)

            if session.busy:
                return JSONResponse({"status": 409, "code": "busy", "message": "该对话正在处理中，请稍后或先 interrupt"}, status_code=409)

            session.busy = True
            queue: asyncio.Queue = asyncio.Queue()
            session.subscribers.append(queue)
            task = asyncio.create_task(
                self.run_turn(session, payload.content, payload.agent),
                name=f"turn-{conversation_id}",
            )
            session.current_task = task

            def _consume_exception(t: asyncio.Task) -> None:
                if not t.cancelled():
                    t.exception()  # 已通过 error 事件暴露，这里仅避免“never retrieved”告警。

            task.add_done_callback(_consume_exception)

            if not payload.stream:
                try:
                    await task
                except (AgentNotFoundError, NoDefaultAgentError) as exc:
                    return JSONResponse(
                        {"status": 404, "code": "agent_not_found", "message": str(exc)},
                        status_code=404,
                    )
                finally:
                    session.subscribers.remove(queue)
                    session.busy = False
                    session.current_task = None
                turn_ids = self.trace_store.list_turn_ids(conversation_id)
                turn_id = turn_ids[-1] if turn_ids else ""
                return JSONResponse({"conversation_id": conversation_id, "turn_id": turn_id})

            async def gen():
                try:
                    while True:
                        event = await queue.get()
                        event_type = event.get("type")
                        yield {
                            "event": event_type or "message",
                            "data": json.dumps(event, ensure_ascii=False, default=str),
                        }
                        if event_type == "turn_complete":
                            break
                finally:
                    session.subscribers.remove(queue)
                    session.busy = False
                    session.current_task = None

            # SSE 帧分隔符显式指定为 "\n"：sse-starlette 默认用 "\r\n"，
            # 前端若按 "\n\n" 分帧会永远匹配不到，导致事件全部积压在缓冲区、
            # 直到整个响应结束才一次性显示（表现为长时间"思考中…"，审批也收不到）。
            return EventSourceResponse(gen(), sep="\n")

        async def resolve_approval_route(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = request.path_params["conversation_id"]
            request_id = request.path_params["request_id"]
            try:
                body = await request.json()
            except (ValueError, TypeError):
                body = {}
            decision = str(body.get("decision", "approve")).strip().lower()
            reply = body.get("reply")
            if decision not in {"approve", "deny", "always"}:
                return JSONResponse({"status": 422, "code": "unprocessable", "message": "decision 必须是 approve/deny/always"}, status_code=422)
            session = self._sessions.get(conversation_id)
            if session is None:
                return JSONResponse({"status": 404, "code": "not_found", "message": "对话不存在"}, status_code=404)
            try:
                self.resolve_approval(session, request_id, decision, reply)
            except KeyError:
                return JSONResponse({"status": 404, "code": "not_found", "message": "审批请求不存在或已过期"}, status_code=404)
            return JSONResponse({"request_id": request_id, "resolved": True})

        async def interrupt(request: Request):
            denied = await _auth(request)
            if denied:
                return denied
            conversation_id = request.path_params["conversation_id"]
            session = self._sessions.get(conversation_id)
            if session is None or session.current_task is None:
                return JSONResponse({"status": 404, "code": "not_found", "message": "没有正在运行的任务"}, status_code=404)
            session.current_task.cancel()
            return JSONResponse({"conversation_id": conversation_id, "interrupted": True})

        async def websocket_stream(websocket):
            await websocket.accept()
            conversation_id = websocket.path_params["conversation_id"]
            session = await self.get_or_create_session(conversation_id, create=True)
            queue: asyncio.Queue = asyncio.Queue()
            session.subscribers.append(queue)

            async def sender():
                while True:
                    event = await queue.get()
                    await websocket.send_text(json.dumps(event, ensure_ascii=False, default=str))

            send_task = asyncio.create_task(sender())
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                send_task.cancel()
                session.subscribers.remove(queue)
                with contextlib.suppress(Exception):
                    await websocket.close()

        routes = [
            Route("/healthz", healthz, methods=["GET"]),
            Route("/api/v1/agents", list_agents, methods=["GET"]),
            Route("/api/v1/conversations", create_conversation, methods=["POST"]),
            Route("/api/v1/conversations", list_conversations_route, methods=["GET"]),
            Route("/api/v1/conversations/{conversation_id}", get_conversation, methods=["GET"]),
            Route("/api/v1/conversations/{conversation_id}/messages", post_message, methods=["POST"]),
            Route(
                "/api/v1/conversations/{conversation_id}/turns/{turn_id}",
                get_turn,
                methods=["GET"],
            ),
            Route(
                "/api/v1/conversations/{conversation_id}/approvals/{request_id}",
                resolve_approval_route,
                methods=["POST"],
            ),
            Route("/api/v1/conversations/{conversation_id}/interrupt", interrupt, methods=["POST"]),
            WebSocketRoute("/api/v1/conversations/{conversation_id}/stream", websocket_stream),
        ]

        # 可选静态目录
        if self.webapp.static_dir:
            from starlette.staticfiles import StaticFiles

            static_path = Path(self.webapp.static_dir).expanduser().resolve()
            if static_path.is_dir():
                routes.append(Mount("/", app=StaticFiles(directory=str(static_path), html=True)))

        return Starlette(routes=routes, middleware=middleware)

    # ------------------------------------------------------------------
    # 进程入口
    # ------------------------------------------------------------------

    def run(self, host: str | None = None, port: int | None = None) -> None:
        import uvicorn

        # 确保观测日志输出到控制台（默认 root logger 是 WARNING，info 会被吞掉），
        # 便于定位"前端只有思考中…"时事件断在哪一环。
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        )
        logging.getLogger("openharness.api.openai_client").setLevel(logging.INFO)
        logging.getLogger("qaapp.service").setLevel(logging.INFO)

        resolved_host = host or self.webapp.host
        resolved_port = port if port is not None else self.webapp.port

        # 安全兜底：默认监听所有网卡且未配 token 时，同网段任何人都能调用对话 API，
        # 进而触发文件写入 / shell 执行。这里给出醒目告警而不是静默开放。
        if resolved_host in {"0.0.0.0", "::", ""} and not self.webapp.api_tokens:
            logger.warning(
                "安全告警：服务监听 %s:%s（所有网卡）但 api_tokens 为空——"
                "同一局域网内任何人都能无需鉴权调用对话 API，并触发文件写入 / shell 执行。"
                "请设置 api_tokens（见 README §9.1），或把 webapp.json 的 host 改回 127.0.0.1。",
                resolved_host,
                resolved_port,
            )

        uvicorn.run(
            self.app,
            host=resolved_host,
            port=resolved_port,
            log_level="info",
        )


__all__ = [
    "ConversationSession",
    "PendingRequest",
    "QaWebService",
]

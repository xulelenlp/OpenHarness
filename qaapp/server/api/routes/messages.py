"""Message execution, approval, interrupt and WebSocket routes."""

from __future__ import annotations

import asyncio
import contextlib
import json

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from qaapp.agents.router import AgentNotFoundError, NoDefaultAgentError
from qaapp.server.api.context import ApiContext
from qaapp.server.runtime.events import sse_turn_stream
from qaapp.workspace.files import AttachmentError, AttachmentNotFound


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service

    async def post_message(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return ctx.error(400, "bad_request", "请求体不是合法 JSON")
        try:
            from qaapp.server.schemas import SendMessageRequest

            payload = SendMessageRequest.model_validate(body)
        except ValueError as exc:
            return ctx.error(422, "unprocessable", str(exc))

        if not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found()
        limit = service.webapp.attachments.max_files_per_message
        if len(payload.attachment_ids) > limit:
            return ctx.error(
                422, "too_many_files", f"单条消息最多携带 {limit} 个附件"
            )

        session = await service.get_or_create_session(conversation_id, create=True)
        if session.busy:
            return ctx.error(409, "busy", "该对话正在处理中，请稍后或先 interrupt")

        try:
            # 附件先校验：id 不存在/不属于本会话时直接返回 404/422，
            # 而不是把错误塞进 SSE 流里让前端只能看到一条红条。
            service.resolve_attachments(session, payload.attachment_ids)
        except AttachmentNotFound as exc:
            return ctx.error(404, "attachment_not_found", str(exc))
        except AttachmentError as exc:
            return ctx.error(422, exc.code, str(exc))

        session.busy = True
        queue: asyncio.Queue = asyncio.Queue()
        session.subscribers.append(queue)
        task = asyncio.create_task(
            service.run_turn(session, payload.content, payload.agent, payload.attachment_ids),
            name=f"turn-{conversation_id}",
        )
        session.current_task = task

        def _consume_exception(t: asyncio.Task) -> None:
            if not t.cancelled():
                t.exception()  # 已通过 error 事件暴露，这里仅避免 never retrieved 告警
            # 兜底：任务在 run_turn 的 try 之前就被取消等极端情况下，也别把会话
            # 永久卡在 busy 上。正常路径由 run_turn 的 finally 收尾。
            if session.current_task is t:
                session.current_task = None
                session.busy = False
                session.busy_since = None

        task.add_done_callback(_consume_exception)

        if not payload.stream:
            try:
                await task
            except (AgentNotFoundError, NoDefaultAgentError) as exc:
                return ctx.error(404, "agent_not_found", str(exc))
            finally:
                with contextlib.suppress(ValueError):
                    session.subscribers.remove(queue)
            turn_ids = service.trace_store.list_turn_ids(conversation_id)
            turn_id = turn_ids[-1] if turn_ids else ""
            return JSONResponse({"conversation_id": conversation_id, "turn_id": turn_id})

        async def gen():
            # 订阅生命周期（含客户端断开时退订）由 sse_turn_stream 负责；
            # 这里不再动 busy / current_task，否则刷新页面会让后台回合“假结束”。
            stream = sse_turn_stream(session, queue)
            try:
                async for event in stream:
                    yield {
                        "event": event.get("type") or "message",
                        "data": json.dumps(event, ensure_ascii=False, default=str),
                    }
            finally:
                await stream.aclose()

        # SSE 帧分隔符显式指定为 "\n"：sse-starlette 默认用 "\r\n"，
        # 前端按 "\n\n" 分帧会永远匹配不到。
        return EventSourceResponse(gen(), sep="\n")

    async def resolve_approval_route(request: Request):
        denied = await ctx.auth(request)
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
            return ctx.error(422, "unprocessable", "decision 必须是 approve/deny/always")
        session = service._sessions.get(conversation_id)
        if session is None or not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found()
        try:
            service.resolve_approval(session, request_id, decision, reply)
        except KeyError:
            return ctx.error(404, "not_found", "审批请求不存在或已过期")
        return JSONResponse({"request_id": request_id, "resolved": True})

    async def interrupt(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        session = service._sessions.get(conversation_id)
        if session is None or session.current_task is None:
            return ctx.error(404, "not_found", "没有正在运行的任务")
        session.current_task.cancel()
        return JSONResponse({"conversation_id": conversation_id, "interrupted": True})

    async def websocket_stream(websocket):
        await websocket.accept()
        conversation_id = websocket.path_params["conversation_id"]
        if not ctx.valid_conversation_id(conversation_id):
            await websocket.close(code=1008, reason="对话不存在")
            return
        session = await service.get_or_create_session(conversation_id, create=True)
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

    return [
        Route(
            "/api/v1/conversations/{conversation_id}/messages",
            post_message,
            methods=["POST"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/approvals/{request_id}",
            resolve_approval_route,
            methods=["POST"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/interrupt",
            interrupt,
            methods=["POST"],
        ),
        WebSocketRoute(
            "/api/v1/conversations/{conversation_id}/stream", websocket_stream
        ),
    ]

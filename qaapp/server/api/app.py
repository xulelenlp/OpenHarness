"""Starlette application assembly for the qaapp web service."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from qaapp.agents.router import AgentNotFoundError, NoDefaultAgentError
from qaapp.server.constants import UPLOAD_CHUNK_BYTES
from qaapp.workspace.files import (
    CONVERSATION_ID_PATTERN,
    AttachmentError,
    AttachmentNotFound,
    AttachmentTooLarge,
    TooManyAttachments,
    UnsupportedAttachment,
    ensure_supported,
    list_files,
    public_record,
    save_upload,
)

logger = logging.getLogger(__name__)

__all__ = ["build_app"]


def build_app(service) -> Starlette:
    def _error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            {"status": status, "code": code, "message": message}, status_code=status
        )

    def _not_found(message: str = "对话不存在") -> JSONResponse:
        return _error(404, "not_found", message)

    def _valid_conversation_id(conversation_id: str) -> bool:
        return bool(CONVERSATION_ID_PATTERN.match(conversation_id or ""))

    async def _iter_upload(upload: UploadFile):
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=service.webapp.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            # 浏览器会话走 Cookie；前后端分机部署时必须把 cors_origins 配成
            # 明确来源（不能是 "*"），浏览器才会在跨域请求里带上 Cookie。
            allow_credentials=True,
        )
    ]

    async def _auth(request: Request):
        """鉴权：优先 Bearer（API 客户端），其次浏览器会话 Cookie。

        Cookie 由 ``GET /api/v1/bootstrap`` 用 ``api_tokens`` 的存在性签发，
        值本身是进程内随机串——**不是 token**，也不进入 JS 可读范围。
        """
        tokens = service.webapp.api_tokens
        if not tokens:
            return None
        header = request.headers.get("authorization", "")
        if header:
            if not header.startswith("Bearer "):
                return JSONResponse(
                    {"status": 401, "code": "unauthorized", "message": "Authorization 必须是 Bearer token"},
                    status_code=401,
                )
            token = header[len("Bearer "):].strip()
            if token not in tokens:
                return JSONResponse(
                    {"status": 403, "code": "forbidden", "message": "token 无效"},
                    status_code=403,
                )
            return None
        browser_auth = service.webapp.browser_auth
        cookie = request.cookies.get(browser_auth.cookie_name, "")
        if (
            browser_auth.enabled
            and cookie
            and secrets.compare_digest(cookie, service._browser_session_value)
        ):
            return None
        return JSONResponse(
            {
                "status": 401,
                "code": "unauthorized",
                "message": "缺少 Bearer token 或浏览器会话（请刷新页面重新建立会话）",
            },
            status_code=401,
        )

    # --- 路由处理 ---

    async def healthz(request: Request):
        return JSONResponse({"status": "ok", "active_sessions": len(service._sessions)})

    async def bootstrap(request: Request):
        """浏览器启动引导：读取服务端配置的 token，签发 HttpOnly 会话 Cookie。

        - 未配置 ``api_tokens``：``auth_required=false``，前端不需要任何凭据；
        - 配置了 ``api_tokens`` 且 ``browser_auth.enabled``：签发 Cookie，
          前端后续请求自动携带，**用户不再需要手填 token**；
        - ``browser_auth.enabled=false``：不签发，前端需用 VITE_API_TOKEN
          或在反向代理层注入 Authorization。
        """
        browser_auth = service.webapp.browser_auth
        auth_required = bool(service.webapp.api_tokens)
        can_session = bool(auth_required and browser_auth.enabled)
        payload: dict[str, Any] = {
            "auth_required": auth_required,
            "browser_session": can_session,
            "session_ttl_seconds": browser_auth.ttl_seconds if can_session else 0,
        }
        headers = {"Cache-Control": "no-store"}
        if not can_session:
            return JSONResponse(payload, headers=headers)

        # 阻止「跨站页面静默建立会话」；curl / 服务端调用没有该头，保持可用。
        site = request.headers.get("sec-fetch-site", "").lower()
        if site and site not in {"same-origin", "same-site", "none"}:
            return _error(403, "cross_site", "不允许从跨站页面建立浏览器会话")

        response = JSONResponse(payload, headers=headers)
        response.set_cookie(
            browser_auth.cookie_name,
            service._browser_session_value,
            max_age=browser_auth.ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

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
                    for a in service.registry.agents
                ]
            }
        )

    async def create_conversation(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = uuid4().hex[:12]
        await service.get_or_create_session(conversation_id, create=True)
        service._persist_empty_conversation(conversation_id)
        return JSONResponse({"conversation_id": conversation_id}, status_code=201)

    async def list_conversations_route(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        limit = int(request.query_params.get("limit", "50"))
        offset = int(request.query_params.get("offset", "0"))
        return JSONResponse({"conversations": service.list_conversations(limit, offset)})

    async def get_conversation(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        detail = (
            service.conversation_detail(conversation_id)
            if _valid_conversation_id(conversation_id)
            else None
        )
        if detail is None:
            return _not_found()
        session = service._sessions.get(conversation_id)
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
        if not _valid_conversation_id(conversation_id):
            return _not_found("轮次不存在")
        events = service.trace_store.load_trace(conversation_id, turn_id=turn_id)
        if not events:
            return _not_found("轮次不存在")
        return JSONResponse({"conversation_id": conversation_id, "turn_id": turn_id, "events": events})

    async def upload_attachments(request: Request):
        """上传附件（multipart/form-data，字段名 files，单次 1..2 个）。"""
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not _valid_conversation_id(conversation_id):
            return _not_found()
        config = service.webapp.attachments
        if not config.enabled:
            return _error(403, "attachments_disabled", "附件上传功能已关闭")
        max_files = max(1, config.max_files_per_message)
        max_bytes = int(config.max_file_mb * 1024 * 1024)

        # 先按 Content-Length 粗筛，避免把超大请求读进磁盘。
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes * max_files + 1024 * 1024:
            return _error(
                413,
                "file_too_large",
                f"上传内容超过上限（单文件 {config.max_file_mb:g}MB，最多 {max_files} 个）",
            )
        try:
            form = await request.form(max_files=max_files * 4)
        except Exception as exc:  # noqa: BLE001 - multipart 解析失败统一 400
            logger.warning("multipart 解析失败 conversation=%s: %s", conversation_id, exc)
            return _error(400, "bad_upload", f"上传表单无法解析：{exc}")

        uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        if not uploads:
            return _error(400, "bad_upload", "没有收到文件（表单字段名应为 files）")
        if len(uploads) > max_files:
            return _error(
                413,
                "too_many_files",
                f"单次最多上传 {max_files} 个文件，当前 {len(uploads)} 个",
            )

        session = await service.get_or_create_session(conversation_id, create=True)
        records: list[dict] = []
        for upload in uploads:
            filename = upload.filename or "file"
            try:
                ensure_supported(filename, config.allowed_suffixes)
            except UnsupportedAttachment as exc:
                return _error(exc.status_code, exc.code, str(exc))
            try:
                stored, size = await save_upload(
                    session.paths, filename, _iter_upload(upload), max_bytes=max_bytes
                )
            except AttachmentTooLarge as exc:
                return _error(exc.status_code, exc.code, str(exc))
            except OSError as exc:
                logger.exception("保存上传文件失败 conversation=%s", conversation_id)
                return _error(500, "save_failed", f"保存文件失败：{exc}")

            record = await service._parse_stored_upload(session, stored, size)
            try:
                await session.attachments.add(
                    record, max_files=config.max_files_per_conversation
                )
            except TooManyAttachments as exc:
                for relative in (record.get("stored_path"), record.get("extracted_path")):
                    if not relative:
                        continue
                    with contextlib.suppress(ValueError, OSError):
                        session.paths.resolve_within(str(relative)).unlink(missing_ok=True)
                return _error(exc.status_code, exc.code, str(exc))
            records.append(record)

        payload = []
        for record in records:
            item = public_record(record)
            item["preview"] = record.get("preview", "")
            payload.append(item)
        return JSONResponse(
            {"conversation_id": conversation_id, "attachments": payload}, status_code=201
        )

    async def get_workspace(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not _valid_conversation_id(conversation_id):
            return _not_found()
        session = await service.get_or_create_session(conversation_id, create=True)
        files, total, truncated = list_files(session.paths, limit=500)
        return JSONResponse(
            {
                "conversation_id": conversation_id,
                "dir": str(session.paths.root),
                "files": files,
                "total_bytes": total,
                "truncated": truncated,
            }
        )

    async def download_file(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not _valid_conversation_id(conversation_id):
            return _not_found()
        session = await service.get_or_create_session(conversation_id, create=True)
        try:
            target = session.paths.resolve_within(request.path_params["file_path"])
        except ValueError:
            return _not_found("文件不存在")
        if not target.is_file():
            return _not_found("文件不存在")
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=target.name,
        )

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
            from qaapp.server.schemas import SendMessageRequest

            payload = SendMessageRequest.model_validate(body)
        except ValueError as exc:
            return JSONResponse({"status": 422, "code": "unprocessable", "message": str(exc)}, status_code=422)

        if not _valid_conversation_id(conversation_id):
            return _not_found()
        limit = service.webapp.attachments.max_files_per_message
        if len(payload.attachment_ids) > limit:
            return JSONResponse(
                {
                    "status": 422,
                    "code": "too_many_files",
                    "message": f"单条消息最多携带 {limit} 个附件",
                },
                status_code=422,
            )

        session = await service.get_or_create_session(conversation_id, create=True)

        if session.busy:
            return JSONResponse({"status": 409, "code": "busy", "message": "该对话正在处理中，请稍后或先 interrupt"}, status_code=409)

        try:
            # 附件先校验一遍：id 不存在/不属于本会话时直接返回 404/422，
            # 而不是把错误塞进 SSE 流里让前端只能看到一条红条。
            service.resolve_attachments(session, payload.attachment_ids)
        except AttachmentNotFound as exc:
            return JSONResponse(
                {"status": 404, "code": "attachment_not_found", "message": str(exc)},
                status_code=404,
            )
        except AttachmentError as exc:
            return JSONResponse(
                {"status": 422, "code": exc.code, "message": str(exc)}, status_code=422
            )

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
            turn_ids = service.trace_store.list_turn_ids(conversation_id)
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
        session = service._sessions.get(conversation_id)
        if session is None or not _valid_conversation_id(conversation_id):
            return _not_found()
        try:
            service.resolve_approval(session, request_id, decision, reply)
        except KeyError:
            return JSONResponse({"status": 404, "code": "not_found", "message": "审批请求不存在或已过期"}, status_code=404)
        return JSONResponse({"request_id": request_id, "resolved": True})

    async def interrupt(request: Request):
        denied = await _auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        session = service._sessions.get(conversation_id)
        if session is None or session.current_task is None:
            return JSONResponse({"status": 404, "code": "not_found", "message": "没有正在运行的任务"}, status_code=404)
        session.current_task.cancel()
        return JSONResponse({"conversation_id": conversation_id, "interrupted": True})

    async def websocket_stream(websocket):
        await websocket.accept()
        conversation_id = websocket.path_params["conversation_id"]
        if not _valid_conversation_id(conversation_id):
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

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        # 免鉴权：浏览器据此建立会话，用户不再手填 token（见 BrowserAuthConfig）。
        Route("/api/v1/bootstrap", bootstrap, methods=["GET"]),
        Route("/api/v1/agents", list_agents, methods=["GET"]),
        Route("/api/v1/conversations", create_conversation, methods=["POST"]),
        Route("/api/v1/conversations", list_conversations_route, methods=["GET"]),
        Route("/api/v1/conversations/{conversation_id}", get_conversation, methods=["GET"]),
        Route("/api/v1/conversations/{conversation_id}/messages", post_message, methods=["POST"]),
        Route(
            "/api/v1/conversations/{conversation_id}/attachments",
            upload_attachments,
            methods=["POST"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/workspace",
            get_workspace,
            methods=["GET"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/files/{file_path:path}",
            download_file,
            methods=["GET"],
        ),
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
    if service.webapp.static_dir:
        from starlette.staticfiles import StaticFiles

        static_path = Path(service.webapp.static_dir).expanduser().resolve()
        if static_path.is_dir():
            routes.append(Mount("/", app=StaticFiles(directory=str(static_path), html=True)))

    return Starlette(routes=routes, middleware=middleware)

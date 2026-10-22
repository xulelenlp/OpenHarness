"""Conversation lifecycle and history routes."""

from __future__ import annotations

from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from qaapp.server.api.context import ApiContext


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service

    async def create_conversation(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = uuid4().hex[:12]
        await service.get_or_create_session(conversation_id, create=True)
        service._persist_empty_conversation(conversation_id)
        return JSONResponse({"conversation_id": conversation_id}, status_code=201)

    async def list_conversations_route(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        limit = int(request.query_params.get("limit", "50"))
        offset = int(request.query_params.get("offset", "0"))
        return JSONResponse({"conversations": service.list_conversations(limit, offset)})

    async def get_conversation(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        detail = (
            service.conversation_detail(conversation_id)
            if ctx.valid_conversation_id(conversation_id)
            else None
        )
        if detail is None:
            return ctx.not_found()
        session = service._sessions.get(conversation_id)
        detail["pending_approvals"] = [
            {"request_id": r.request_id, "kind": r.kind, "payload": r.payload}
            for r in (session.pending.values() if session else [])
        ]
        return JSONResponse(detail)

    async def get_turn(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        turn_id = request.path_params["turn_id"]
        if not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found("轮次不存在")
        events = service.trace_store.load_trace(conversation_id, turn_id=turn_id)
        if not events:
            return ctx.not_found("轮次不存在")
        return JSONResponse(
            {"conversation_id": conversation_id, "turn_id": turn_id, "events": events}
        )

    return [
        Route("/api/v1/conversations", create_conversation, methods=["POST"]),
        Route("/api/v1/conversations", list_conversations_route, methods=["GET"]),
        Route(
            "/api/v1/conversations/{conversation_id}",
            get_conversation,
            methods=["GET"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/turns/{turn_id}",
            get_turn,
            methods=["GET"],
        ),
    ]

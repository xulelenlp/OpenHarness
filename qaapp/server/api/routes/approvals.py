"""Approval state shared across live conversations.

审批卡片过去只通过发起该轮的 SSE 响应下发：页面刷新 / 切换会话后连接断了，
请求就再也到不了浏览器，只能等 300s 超时。这里提供一个与具体连接无关的
只读快照，前端轮询它来恢复卡片、侧栏角标与断线跟进。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from qaapp.server.api.context import ApiContext


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service

    async def list_approvals(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        return JSONResponse(service.approval_snapshot())

    return [
        Route("/api/v1/approvals", list_approvals, methods=["GET"]),
    ]

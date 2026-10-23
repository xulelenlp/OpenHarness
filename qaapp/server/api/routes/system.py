"""Health, browser bootstrap and agent listing routes."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from qaapp.server.api.context import ApiContext


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service

    async def healthz(request: Request):
        return JSONResponse({"status": "ok", "active_sessions": len(service._sessions)})

    async def bootstrap(request: Request):
        """前端启动信息：是否需要登录 / 是否签发快捷会话 Cookie。"""
        browser_auth = service.webapp.browser_auth
        account_auth = service.auth
        login_required = bool(account_auth.enabled)
        auth_required = bool(service.webapp.api_tokens or login_required)
        # 启用账户登录后，不再用 bootstrap 免密签发会话，登录必须走 /auth/login。
        can_session = bool(auth_required and browser_auth.enabled and not login_required)
        payload: dict[str, Any] = {
            "auth_required": auth_required,
            "login_required": login_required,
            "auth_enabled": login_required,
            "default_credentials": (
                account_auth.default_passwords if login_required else False
            ),
            "browser_session": can_session,
            "session_ttl_seconds": browser_auth.ttl_seconds if can_session else 0,
        }
        headers = {"Cache-Control": "no-store"}
        if not can_session:
            return JSONResponse(payload, headers=headers)

        # 阻止跨站页面静默建立会话；curl / 服务端调用没有该头，保持可用。
        site = request.headers.get("sec-fetch-site", "").lower()
        if site and site not in {"same-origin", "same-site", "none"}:
            return ctx.error(403, "cross_site", "不允许从跨站页面建立浏览器会话")

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
        return JSONResponse({"agents": ctx.registry_agents()})

    return [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/api/v1/bootstrap", bootstrap, methods=["GET"]),
        Route("/api/v1/agents", list_agents, methods=["GET"]),
    ]

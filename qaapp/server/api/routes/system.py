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
        """Issue an HttpOnly browser session cookie when api_tokens are set."""
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

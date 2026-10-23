"""API 鉴权中间件：默认拒绝、角色校验、Cookie 请求的跨站防护。"""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from qaapp.server.auth import ROLE_ADMIN, AuthUser

#: 未登录也可访问的接口（登录页 / 健康检查 / 前端启动信息）。
PUBLIC_API_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/bootstrap",
    "/healthz",
}

#: ``viewer`` 角色也允许调用的写接口（退出登录、修改自己的密码）。
VIEWER_WRITE_ALLOWED = {
    "/api/v1/auth/logout",
    "/api/v1/auth/password",
}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"status": status, "code": code, "message": message}, status_code=status
    )


def _host_matches_origin(request: Request) -> bool:
    """Cookie 鉴权的写请求必须来自同源页面。

    浏览器对同源 POST 也会带 ``Origin``；缺失时（curl / 反向代理注入）放行，
    因为跨站表单会被 ``SameSite=Lax`` 拒绝携带会话 Cookie。
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    host = request.headers.get("host", "")
    origin_host = urlsplit(origin).netloc
    return bool(host) and origin_host == host


def _bearer_user(request: Request, tokens: list[str]) -> AuthUser | None:
    header = request.headers.get("authorization", "")
    if not header:
        return None
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer ") :].strip()
    if token and token in tokens:
        return AuthUser(
            username="api-token",
            role=ROLE_ADMIN,
            display_name="API Token",
            via="bearer",
        )
    return None


class AuthMiddleware:
    """仅在 ``webapp.auth.enabled`` 时生效；否则保持旧的 token 行为。"""

    def __init__(self, app, service) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        auth = getattr(self.service, "auth", None)
        if auth is None or not auth.enabled:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, auth)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if not path.startswith("/api/") or path in PUBLIC_API_PATHS or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        user: AuthUser | None = None
        bearer_header = request.headers.get("authorization", "")
        if bearer_header:
            user = _bearer_user(request, self.service.webapp.api_tokens)
            if user is None:
                response = error_response(403, "forbidden", "token 无效")
                await response(scope, receive, send)
                return
        else:
            token = request.cookies.get(auth.cookie_name, "")
            user = auth.resolve(token)
            if user is None:
                response = error_response(
                    401, "unauthorized", "登录已失效，请重新登录"
                )
                await response(scope, receive, send)
                return

        if user.role != ROLE_ADMIN and method not in SAFE_METHODS:
            if path not in VIEWER_WRITE_ALLOWED:
                response = error_response(
                    403, "forbidden", "只读账户不能执行该操作"
                )
                await response(scope, receive, send)
                return

        if user.via == "cookie" and method not in SAFE_METHODS:
            if not _host_matches_origin(request):
                response = error_response(403, "cross_site", "跨站请求被拒绝")
                await response(scope, receive, send)
                return

        state = scope.setdefault("state", {})
        state["auth_user"] = user
        await self.app(scope, receive, send)

    async def _websocket(self, scope, receive, send, auth) -> None:
        """WebSocket 与 HTTP 用同一份会话 Cookie；未登录直接 1008 关闭。"""
        path = scope.get("path", "")
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_cookie = headers.get(b"cookie", b"").decode("latin-1")
        jar = SimpleCookie()
        try:
            jar.load(raw_cookie)
        except Exception:  # noqa: BLE001 - 非法 Cookie 按未登录处理
            jar = SimpleCookie()
        morsel = jar.get(auth.cookie_name)
        user = auth.resolve(morsel.value if morsel else "")
        if user is None:
            await send({"type": "websocket.close", "code": 1008, "reason": "unauthorized"})
            return
        scope.setdefault("state", {})["auth_user"] = user
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """给所有响应补基础安全头（不引入 CSP，避免误伤前端）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                for key, value in (
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ):
                    if key not in existing:
                        headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def json_body(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}

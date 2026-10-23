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


def _hostname(netloc: str) -> str:
    """取 ``host[:port]`` 里的主机名（兼容 IPv6 字面量）。"""
    return (urlsplit(f"//{netloc.strip()}").hostname or "").lower()


def _normalized_origin(value: str) -> tuple[str, str] | None:
    try:
        parts = urlsplit(value.strip())
    except ValueError:  # pragma: no cover - urlsplit 很少抛错
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return parts.scheme.lower(), parts.netloc.lower()


def origin_allowed(request: Request, trusted_origins: set[tuple[str, str]]) -> bool:
    """判断带 Cookie 的写请求（以及登录）是否来自可信页面。

    判定顺序（任一条满足即通过）：

    1. 没有 ``Origin``（curl / 反向代理注入）：只要求 ``Sec-Fetch-Site`` 不是
       ``cross-site``；跨站表单本身也会被 ``SameSite=Lax`` 拒绝携带会话 Cookie。
    2. ``Origin`` 在 ``webapp.json: cors_origins`` 里被显式放行：用于前端与后端
       分域名部署（``cors_origins`` 里写 ``"*"`` 不等于放行，避免退化成无防护）。
    3. 浏览器声明 ``Sec-Fetch-Site: same-origin``：页面与请求同源，代理改写
       ``Host`` 时依然可信（该头由浏览器生成，页面无法伪造）。
    4. ``Origin`` 与 ``Host`` **主机名一致**（允许端口不同）：开发态前端跑在
       5174、后端在 8787，或 Vite/Nginx 代理保留 ``Host`` 时都属此类。
       仅主机名相等仍能挡住来自其它站点的 CSRF。

    ``Sec-Fetch-Site: cross-site`` 只有显式放行的来源可以通过。
    """
    site = request.headers.get("sec-fetch-site", "").strip().lower()
    origin = request.headers.get("origin", "")
    if not origin:
        return site != "cross-site"
    normalized = _normalized_origin(origin)
    if normalized is None:  # 例如 "Origin: null"
        return False
    if normalized in trusted_origins:
        return True
    if site == "same-origin":
        return True
    if site == "cross-site":
        return False
    host = request.headers.get("host", "")
    origin_hostname = _hostname(normalized[1])
    host_hostname = _hostname(host)
    return bool(origin_hostname) and origin_hostname == host_hostname


def trusted_origins(cors_origins: list[str] | None) -> set[tuple[str, str]]:
    """把 ``cors_origins`` 转成可比较的来源集合；忽略 ``"*"``。"""
    trusted: set[tuple[str, str]] = set()
    for item in cors_origins or ():
        normalized = _normalized_origin(item)
        if normalized is not None:
            trusted.add(normalized)
    return trusted


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

    def _allowed_origins(self) -> set[tuple[str, str]]:
        webapp = getattr(self.service, "webapp", None)
        return trusted_origins(getattr(webapp, "cors_origins", None))

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
            if not origin_allowed(request, self._allowed_origins()):
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

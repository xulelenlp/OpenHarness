"""临时账户登录路由（TEMP-AUTH）。

正式部署接入 OSS / SSO 后：删除本文件、``qaapp/server/auth.py``、
``qaapp/server/api/auth_middleware.py`` 与前端 ``LoginPage``，把
``AuthMiddleware`` 换成 OSS 回调校验即可。详见 docs/qaapp/AUTH.zh-CN.md。
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from qaapp.server.api.auth_middleware import json_body
from qaapp.server.api.context import ApiContext
from qaapp.server.auth import AuthError

_MAX_BODY_BYTES = 8192


def _is_https(request: Request, trust_proxy: bool) -> bool:
    if request.url.scheme == "https":
        return True
    if trust_proxy:
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        return proto == "https"
    return False


def _client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else ""


def _set_session_cookie(
    response: JSONResponse, request: Request, service, token: str
) -> None:
    auth = service.auth
    response.set_cookie(
        auth.cookie_name,
        token,
        max_age=auth.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=_is_https(request, auth.trust_proxy_headers),
        path="/",
    )


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service
    auth = service.auth

    async def login(request: Request):
        if not auth.enabled:
            return ctx.error(404, "not_found", "未启用账户登录")
        # 防登录 CSRF：跨站页面不能把用户静默登录到别的账号。
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return ctx.error(403, "cross_site", "不允许从跨站页面登录")
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            return ctx.error(413, "payload_too_large", "请求体过大")
        payload = json_body(raw)
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if not username or not password:
            return ctx.error(400, "invalid_request", "请输入用户名和密码")
        try:
            token, user = auth.login(
                username,
                password,
                ip=_client_ip(request, auth.trust_proxy_headers),
            )
        except AuthError as exc:
            headers = (
                {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            )
            return JSONResponse(
                {"status": exc.status, "code": exc.code, "message": exc.message},
                status_code=exc.status,
                headers=headers,
            )
        body: dict[str, Any] = {
            "user": user.as_dict(),
            "expires_in": auth.session_ttl_seconds,
            "default_password": auth.default_passwords,
        }
        response = JSONResponse(body, headers={"Cache-Control": "no-store"})
        _set_session_cookie(response, request, service, token)
        return response

    async def logout(request: Request):
        token = request.cookies.get(auth.cookie_name, "")
        auth.revoke(token)
        response = JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(auth.cookie_name, path="/")
        return response

    async def me(request: Request):
        if not auth.enabled:
            return JSONResponse(
                {"auth_enabled": False, "user": None},
                headers={"Cache-Control": "no-store"},
            )
        user = getattr(request.state, "auth_user", None)
        if user is None:
            return ctx.error(401, "unauthorized", "未登录")
        return JSONResponse(
            {
                "auth_enabled": True,
                "user": user.as_dict(),
                "default_password": auth.default_passwords,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def change_password(request: Request):
        if not auth.enabled:
            return ctx.error(404, "not_found", "未启用账户登录")
        user = getattr(request.state, "auth_user", None)
        if user is None:
            return ctx.error(401, "unauthorized", "未登录")
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            return ctx.error(413, "payload_too_large", "请求体过大")
        payload = json_body(raw)
        old_password = str(payload.get("old_password") or "")
        new_password = str(payload.get("new_password") or "")
        try:
            auth.change_password(user.username, old_password, new_password)
        except AuthError as exc:
            return ctx.error(exc.status, exc.code, exc.message)
        # 改密后吊销该用户全部会话（含当前），强制重新登录。
        response = JSONResponse(
            {"status": "ok", "relogin": True}, headers={"Cache-Control": "no-store"}
        )
        response.delete_cookie(auth.cookie_name, path="/")
        return response

    return [
        Route("/api/v1/auth/login", login, methods=["POST"]),
        Route("/api/v1/auth/logout", logout, methods=["POST"]),
        Route("/api/v1/auth/me", me, methods=["GET"]),
        Route("/api/v1/auth/password", change_password, methods=["POST"]),
    ]

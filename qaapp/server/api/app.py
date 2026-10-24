"""Starlette application assembly for the qaapp web service.

The HTTP surface is unchanged; handlers live in ``qaapp.server.api.routes`` so
this module only wires middleware, routes and the optional static frontend.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount, Route

from qaapp.server.api.auth_middleware import AuthMiddleware, SecurityHeadersMiddleware
from qaapp.server.api.context import ApiContext
from qaapp.server.api.routes import approvals, conversations, files, messages, system
from qaapp.server.api.routes import auth as auth_routes

__all__ = ["build_app"]


def build_app(service) -> Starlette:
    ctx = ApiContext(service)
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=service.webapp.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            # 浏览器会话走 Cookie；前后端分机部署时必须把 cors_origins 配成
            # 明确来源（不能是 "*"），浏览器才会在跨域请求里带上 Cookie。
            allow_credentials=True,
        ),
        Middleware(SecurityHeadersMiddleware),
    ]
    # 临时账户登录（TEMP-AUTH）：正式接入 OSS / SSO 时替换这一层即可。
    if getattr(service, "auth", None) is not None:
        middleware.append(Middleware(AuthMiddleware, service=service))

    routes: list[Route | Mount] = []
    routes.extend(system.build_routes(ctx))
    routes.extend(auth_routes.build_routes(ctx))
    routes.extend(conversations.build_routes(ctx))
    routes.extend(approvals.build_routes(ctx))
    routes.extend(messages.build_routes(ctx))
    routes.extend(files.build_routes(ctx))

    # 可选静态目录：生产由后端托管前端构建产物。
    if service.webapp.static_dir:
        from starlette.staticfiles import StaticFiles

        static_path = Path(service.webapp.static_dir).expanduser().resolve()
        if static_path.is_dir():
            routes.append(Mount("/", app=StaticFiles(directory=str(static_path), html=True)))

    return Starlette(routes=routes, middleware=middleware)

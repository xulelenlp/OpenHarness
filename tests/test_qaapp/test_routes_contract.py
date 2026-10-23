"""Freeze the public qaapp HTTP route surface."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.routing import Route, WebSocketRoute

from qaapp.server.api.app import build_app

EXPECTED_HTTP = {
    ("/healthz", ("GET",)),
    ("/api/v1/bootstrap", ("GET",)),
    ("/api/v1/auth/login", ("POST",)),
    ("/api/v1/auth/logout", ("POST",)),
    ("/api/v1/auth/me", ("GET",)),
    ("/api/v1/auth/password", ("POST",)),
    ("/api/v1/agents", ("GET",)),
    ("/api/v1/conversations", ("POST",)),
    ("/api/v1/conversations", ("GET",)),
    ("/api/v1/conversations/{conversation_id}", ("GET",)),
    ("/api/v1/conversations/{conversation_id}/messages", ("POST",)),
    ("/api/v1/conversations/{conversation_id}/attachments", ("POST",)),
    ("/api/v1/conversations/{conversation_id}/workspace", ("GET",)),
    ("/api/v1/conversations/{conversation_id}/files/{file_path:path}", ("GET",)),
    ("/api/v1/conversations/{conversation_id}/turns/{turn_id}", ("GET",)),
    ("/api/v1/conversations/{conversation_id}/approvals/{request_id}", ("POST",)),
    ("/api/v1/conversations/{conversation_id}/interrupt", ("POST",)),
}
EXPECTED_WS = {"/api/v1/conversations/{conversation_id}/stream"}


def _fake_service():
    return SimpleNamespace(
        webapp=SimpleNamespace(cors_origins=["*"], static_dir=""),
        auth=SimpleNamespace(
            enabled=False,
            cookie_name="qaapp_auth",
            session_ttl_seconds=3600,
            trust_proxy_headers=False,
            default_passwords=False,
        ),
        _browser_session_value="test",
    )


def test_http_route_surface_is_unchanged():
    app = build_app(_fake_service())
    actual = {
        (route.path, tuple(sorted((route.methods or set()) - {"HEAD"})))
        for route in app.routes
        if isinstance(route, Route)
    }
    assert actual == EXPECTED_HTTP


def test_websocket_route_surface_is_unchanged():
    app = build_app(_fake_service())
    actual = {route.path for route in app.routes if isinstance(route, WebSocketRoute)}
    assert actual == EXPECTED_WS

"""Shared request helpers for qaapp HTTP routes."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse

from qaapp.server.constants import UPLOAD_CHUNK_BYTES
from qaapp.workspace.files import CONVERSATION_ID_PATTERN

if TYPE_CHECKING:
    from qaapp.server.service import QaWebService


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"status": status, "code": code, "message": message}, status_code=status
    )


def not_found_response(message: str = "对话不存在") -> JSONResponse:
    return error_response(404, "not_found", message)


class ApiContext:
    """Per-application helpers shared by route modules."""

    def __init__(self, service: "QaWebService") -> None:
        self.service = service

    def error(self, status: int, code: str, message: str) -> JSONResponse:
        return error_response(status, code, message)

    def not_found(self, message: str = "对话不存在") -> JSONResponse:
        return not_found_response(message)

    @staticmethod
    def valid_conversation_id(conversation_id: str) -> bool:
        return bool(CONVERSATION_ID_PATTERN.match(conversation_id or ""))

    async def auth(self, request: Request) -> JSONResponse | None:
        """Bearer token or browser session cookie; ``None`` means allowed."""
        tokens = self.service.webapp.api_tokens
        if not tokens:
            return None
        header = request.headers.get("authorization", "")
        if header:
            if not header.startswith("Bearer "):
                return error_response(401, "unauthorized", "Authorization 必须是 Bearer token")
            token = header[len("Bearer ") :].strip()
            if token not in tokens:
                return error_response(403, "forbidden", "token 无效")
            return None
        browser_auth = self.service.webapp.browser_auth
        cookie = request.cookies.get(browser_auth.cookie_name, "")
        if (
            browser_auth.enabled
            and cookie
            and secrets.compare_digest(cookie, self.service._browser_session_value)
        ):
            return None
        return error_response(
            401,
            "unauthorized",
            "缺少 Bearer token 或浏览器会话（请刷新页面重新建立会话）",
        )

    @staticmethod
    async def iter_upload(upload: UploadFile):
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    def registry_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": agent.id,
                "kind": agent.kind,
                "name": agent.name,
                "description": agent.description,
                "enabled": agent.enabled,
            }
            for agent in self.service.registry.agents
        ]

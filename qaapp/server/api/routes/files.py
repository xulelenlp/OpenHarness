"""Attachment upload and conversation workspace file routes."""

from __future__ import annotations

import contextlib
import logging

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from qaapp.server.api.context import ApiContext
from qaapp.workspace.files import (
    AttachmentTooLarge,
    TooManyAttachments,
    UnsupportedAttachment,
    ensure_supported,
    list_files,
    public_record,
    save_upload,
)

logger = logging.getLogger(__name__)


def build_routes(ctx: ApiContext) -> list[Route]:
    service = ctx.service

    async def upload_attachments(request: Request):
        """上传附件（multipart/form-data，字段名 files，单次 1..2 个）。"""
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found()
        config = service.webapp.attachments
        if not config.enabled:
            return ctx.error(403, "attachments_disabled", "附件上传功能已关闭")
        max_files = max(1, config.max_files_per_message)
        max_bytes = int(config.max_file_mb * 1024 * 1024)

        # 先按 Content-Length 粗筛，避免把超大请求读进磁盘。
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes * max_files + 1024 * 1024:
            return ctx.error(
                413,
                "file_too_large",
                f"上传内容超过上限（单文件 {config.max_file_mb:g}MB，最多 {max_files} 个）",
            )
        try:
            form = await request.form(max_files=max_files * 4)
        except Exception as exc:  # noqa: BLE001 - multipart 解析失败统一 400
            logger.warning("multipart 解析失败 conversation=%s: %s", conversation_id, exc)
            return ctx.error(400, "bad_upload", f"上传表单无法解析：{exc}")

        uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
        if not uploads:
            return ctx.error(400, "bad_upload", "没有收到文件（表单字段名应为 files）")
        if len(uploads) > max_files:
            return ctx.error(
                413,
                "too_many_files",
                f"单次最多上传 {max_files} 个文件，当前 {len(uploads)} 个",
            )

        session = await service.get_or_create_session(conversation_id, create=True)
        records: list[dict] = []
        for upload in uploads:
            filename = upload.filename or "file"
            try:
                ensure_supported(filename, config.allowed_suffixes)
            except UnsupportedAttachment as exc:
                return ctx.error(exc.status_code, exc.code, str(exc))
            try:
                stored, size = await save_upload(
                    session.paths, filename, ctx.iter_upload(upload), max_bytes=max_bytes
                )
            except AttachmentTooLarge as exc:
                return ctx.error(exc.status_code, exc.code, str(exc))
            except OSError as exc:
                logger.exception("保存上传文件失败 conversation=%s", conversation_id)
                return ctx.error(500, "save_failed", f"保存文件失败：{exc}")

            record = await service._parse_stored_upload(session, stored, size)
            try:
                await session.attachments.add(
                    record, max_files=config.max_files_per_conversation
                )
            except TooManyAttachments as exc:
                for relative in (record.get("stored_path"), record.get("extracted_path")):
                    if not relative:
                        continue
                    with contextlib.suppress(ValueError, OSError):
                        session.paths.resolve_within(str(relative)).unlink(missing_ok=True)
                return ctx.error(exc.status_code, exc.code, str(exc))
            records.append(record)

        payload = []
        for record in records:
            item = public_record(record)
            item["preview"] = record.get("preview", "")
            payload.append(item)
        return JSONResponse(
            {"conversation_id": conversation_id, "attachments": payload}, status_code=201
        )

    async def get_workspace(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found()
        session = await service.get_or_create_session(conversation_id, create=True)
        files, total, truncated = list_files(session.paths, limit=500)
        return JSONResponse(
            {
                "conversation_id": conversation_id,
                "dir": str(session.paths.root),
                "files": files,
                "total_bytes": total,
                "truncated": truncated,
            }
        )

    async def download_file(request: Request):
        denied = await ctx.auth(request)
        if denied:
            return denied
        conversation_id = request.path_params["conversation_id"]
        if not ctx.valid_conversation_id(conversation_id):
            return ctx.not_found()
        session = await service.get_or_create_session(conversation_id, create=True)
        try:
            target = session.paths.resolve_within(request.path_params["file_path"])
        except ValueError:
            return ctx.not_found("文件不存在")
        if not target.is_file():
            return ctx.not_found("文件不存在")
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=target.name,
        )

    return [
        Route(
            "/api/v1/conversations/{conversation_id}/attachments",
            upload_attachments,
            methods=["POST"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/workspace",
            get_workspace,
            methods=["GET"],
        ),
        Route(
            "/api/v1/conversations/{conversation_id}/files/{file_path:path}",
            download_file,
            methods=["GET"],
        ),
    ]

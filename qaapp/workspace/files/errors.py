"""Conversation workspace file primitives."""

from __future__ import annotations


class AttachmentError(Exception):
    """附件操作失败（带 HTTP 语义的状态码）。"""

    status_code = 400
    code = "attachment_error"

class UnsupportedAttachment(AttachmentError):
    status_code = 415
    code = "unsupported_file_type"


class AttachmentTooLarge(AttachmentError):
    status_code = 413
    code = "file_too_large"


class TooManyAttachments(AttachmentError):
    status_code = 413
    code = "too_many_files"


class AttachmentNotFound(AttachmentError):
    status_code = 404
    code = "attachment_not_found"


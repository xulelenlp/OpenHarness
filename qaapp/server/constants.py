"""Shared server constants."""

from __future__ import annotations

#: 用户只上传文件、不输入文字时使用的默认提问。
DEFAULT_ATTACHMENT_PROMPT = "请阅读并分析我上传的文件。"

#: 上传分块大小（流式落盘用）。
UPLOAD_CHUNK_BYTES = 256 * 1024

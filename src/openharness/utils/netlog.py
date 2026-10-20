"""File-backed logging for outbound web tools (``web_search`` / ``web_fetch``).

Every outbound attempt is appended to ``<logs dir>/web.log`` so operators can
download the file from a server and see exactly why a request failed: endpoint,
query, resolution mode, proxy (credential-masked), elapsed time, HTTP status,
exception type, ``args`` and the full ``__cause__`` chain.  The directory is
``OPENHARNESS_LOGS_DIR`` when set, otherwise ``~/.openharness/logs``.
"""

from __future__ import annotations

import logging
import os
import socket
from logging.handlers import RotatingFileHandler

from openharness.config.paths import get_logs_dir

LOGGER_NAME = "openharness.web"
LOG_FILE_NAME = "web.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_MAX_QUERY_CHARS = 160
_MAX_DETAIL_CHARS = 600

_state: dict[str, object] = {"path": None, "handler": None}


def get_web_logger() -> logging.Logger:
    """Return the web logger, attaching a rotating file handler on demand."""
    logger = logging.getLogger(LOGGER_NAME)
    try:
        path = get_logs_dir() / LOG_FILE_NAME
    except OSError:  # pragma: no cover - unwritable logs dir should not break tools
        return logger

    if _state.get("path") != str(path):
        previous = _state.get("handler")
        if isinstance(previous, logging.Handler):
            logger.removeHandler(previous)
            previous.close()
        handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        _state["path"] = str(path)
        _state["handler"] = handler

    logger.setLevel(_log_level())
    return logger


def reset_web_logging() -> None:
    """Detach the cached handler (used by tests and after log dir changes)."""
    logger = logging.getLogger(LOGGER_NAME)
    handler = _state.get("handler")
    if isinstance(handler, logging.Handler):
        logger.removeHandler(handler)
        handler.close()
    _state["path"] = None
    _state["handler"] = None


def log_web_success(
    *,
    tool: str,
    endpoint: str,
    query: str | None,
    elapsed: float,
    status_code: int | None = None,
    result_count: int | None = None,
) -> None:
    """Record one successful outbound request."""
    logger = get_web_logger()
    fields = [f"tool={tool}", "event=ok", f"endpoint={endpoint}"]
    if query is not None:
        fields.append(f'q="{_clip(query)}"')
    fields.append(f"elapsed={elapsed:.2f}s")
    if status_code is not None:
        fields.append(f"status={status_code}")
    if result_count is not None:
        fields.append(f"results={result_count}")
    logger.info(" ".join(fields))


def log_web_failure(
    *,
    tool: str,
    endpoint: str,
    query: str | None,
    elapsed: float,
    exc: BaseException,
    detail: str,
) -> None:
    """Record one failed outbound request with its full diagnosis."""
    logger = get_web_logger()
    fields = [f"tool={tool}", "event=failed", f"endpoint={endpoint}"]
    if query is not None:
        fields.append(f'q="{_clip(query)}"')
    fields.append(f"elapsed={elapsed:.2f}s")
    fields.append(f"type={type(exc).__name__}")
    fields.append(f"message={str(exc).strip() or '<empty>'}")
    fields.append(f"args={exc.args!r}")
    cause = exc.__cause__ or exc.__context__
    fields.append(f"cause={_cause_repr(cause)}")
    resolved = _resolved_addresses(endpoint)
    if resolved:
        fields.append(f"dns={resolved}")
    fields.append(f"detail={_clip(detail, _MAX_DETAIL_CHARS)}")
    logger.warning(" ".join(fields))


def _resolved_addresses(endpoint: str, *, limit: int = 4) -> str:
    """Best-effort DNS snapshot, useful for spotting poisoned answers."""
    from urllib.parse import urlparse

    host = urlparse(endpoint).hostname
    if not host:
        return ""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError as exc:
        return f"<解析失败 {type(exc).__name__}: {exc}>"
    addresses: list[str] = []
    for info in infos:
        candidate = str(info[4][0])
        if candidate not in addresses:
            addresses.append(candidate)
        if len(addresses) >= limit:
            break
    return ",".join(addresses)


def _cause_repr(cause: BaseException | None) -> str:
    if cause is None:
        return "None"
    text = str(cause).strip()
    rendered = f"{type(cause).__name__}({text})" if text else f"{type(cause).__name__}()"
    return _clip(rendered)


def _clip(value: str, limit: int = _MAX_QUERY_CHARS) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _log_level() -> int:
    raw = os.environ.get("OPENHARNESS_LOG_LEVEL", "").strip().upper()
    if raw:
        level = getattr(logging, raw, None)
        if isinstance(level, int):
            return level
    return logging.INFO

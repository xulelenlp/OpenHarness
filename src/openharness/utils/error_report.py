"""Readable failure descriptions for outbound web tools.

The HTTP stack loses information on the way out: ``httpcore`` wraps an
``anyio`` ``TimeoutError`` into ``ConnectTimeout(TimeoutError())`` and ``httpx``
re-emits it as ``ConnectTimeout(str(exc))``, so ``str(exc)`` is an empty string.
Rendering that straight into a tool result produces the useless
``web_search failed: `` message.  This module rebuilds a diagnosis from the
exception type, the cause chain, the target, the elapsed time and the
configured proxy, and adds an actionable hint.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from openharness.utils.network_guard import NetworkGuardError

_EMPTY_MESSAGE = "原始错误信息为空"
_MAX_CAUSE_REPR = 200
_EMPTY_CAUSE_NAMES = {"TimeoutError", "OSError", "BrokenResourceError", "ClosedResourceError"}
_CANCELLATION_NAMES = {"CancelledError", "Cancelled"}


def mask_credentials(value: str | None) -> str:
    """Hide credentials embedded in a proxy URL."""
    if not value:
        return ""
    if "@" not in value:
        return value
    head, _, tail = value.rpartition("@")
    scheme, separator, _ = head.partition("://")
    return f"{scheme}{separator}***@{tail}" if separator else f"***@{tail}"


def configured_web_proxy() -> str | None:
    """Return the proxy the web guard would use, if any."""
    env_proxy = os.environ.get("OPENHARNESS_WEB_PROXY")
    if env_proxy:
        return env_proxy
    try:
        from openharness.config import load_settings

        return load_settings().web.proxy
    except Exception:  # pragma: no cover - diagnostics must never raise
        return None


def target_label(target: str | None) -> str:
    """Return a compact ``host:port`` label for a URL."""
    if not target:
        return "未知目标"
    parsed = urlparse(target)
    if not parsed.hostname:
        return target
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.hostname}:{port}"


def describe_http_exception(
    exc: BaseException,
    *,
    target: str | None = None,
    elapsed: float | None = None,
    proxy: str | None = None,
) -> str:
    """Render one exception as a single diagnostic line.

    Example::

        ConnectTimeout｜原始错误信息为空｜目标 cn.bing.com:443｜耗时 20.1s｜代理 未配置
        ｜原因 httpcore.ConnectTimeout(TimeoutError())
    """
    message = str(exc).strip()
    parts = [type(exc).__name__]
    parts.append(message or _EMPTY_MESSAGE)
    if target:
        parts.append(f"目标 {target_label(target)}")
    if elapsed is not None:
        parts.append(f"耗时 {elapsed:.1f}s")
    masked = mask_credentials(proxy)
    parts.append(f"代理 {masked}" if masked else "代理 未配置")

    cause_chain = _cause_chain(exc)
    if cause_chain:
        parts.append(f"原因 {cause_chain}")
    return "｜".join(parts)


def failure_hint(exc: BaseException, *, status_code: int | None = None) -> str | None:
    """Return an actionable hint for the given failure, if we have one."""
    if isinstance(exc, NetworkGuardError):
        return "提示：目标被网络守卫拦截，按上面的报文调整 web.proxy / web.resolution_mode。"
    if isinstance(exc, httpx.TimeoutException):
        return (
            "提示：连接超时说明这台机器无法直达该地址（防火墙丢包、被墙或需要代理）。"
            "可设置 web.proxy / OPENHARNESS_WEB_PROXY，或把 web.search_url 指向可达的搜索端点。"
        )
    if isinstance(exc, httpx.HTTPStatusError) or status_code is not None:
        code = status_code if status_code is not None else status_code_of(exc)
        rendered = f"HTTP {code}" if code is not None else "HTTP 错误"
        return (
            f"提示：请求返回 {rendered}，通常是被反爬拦截或端点不可用；"
            "可改用可直连的端点（默认 cn.bing.com）。"
        )
    if isinstance(exc, httpx.ConnectError):
        return "提示：连接失败，请检查 DNS 解析、出网策略与代理设置（web.proxy / OPENHARNESS_WEB_PROXY）。"
    if isinstance(exc, httpx.HTTPError):
        return "提示：HTTP 请求失败，可用 scripts/diag_web.py 在目标机器上复现并定位。"
    return None


def status_code_of(exc: BaseException) -> int | None:
    """Return the HTTP status code carried by an exception, if any."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)



def _cause_chain(exc: BaseException, *, limit: int = 3) -> str:
    rendered: list[str] = []
    current = exc.__cause__ or exc.__context__
    while current is not None and len(rendered) < limit:
        name = type(current).__name__
        if name in _CANCELLATION_NAMES:
            # anyio/asyncio cancellation noise; the useful part is above it.
            break
        text = str(current).strip()
        if text:
            rendered.append(f"{name}({text[:_MAX_CAUSE_REPR]})")
        elif name in _EMPTY_CAUSE_NAMES:
            rendered.append(f"{name}()")
        else:
            rendered.append(name)
        current = current.__cause__ or current.__context__
    return " <- ".join(rendered)

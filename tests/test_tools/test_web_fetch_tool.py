"""Tests for web fetch and search tools."""

from __future__ import annotations

import time

import httpx
import pytest

from openharness.tools.base import ToolExecutionContext
from openharness.tools.web_fetch_tool import WebFetchTool, WebFetchToolInput, _html_to_text
from openharness.tools.web_search_tool import (
    WebSearchTool,
    WebSearchToolInput,
    normalize_result_url,
)
from openharness.utils.netlog import reset_web_logging
from openharness.utils.network_guard import fetch_public_http_response


@pytest.mark.asyncio
async def test_web_fetch_tool_reads_html(tmp_path, monkeypatch):
    async def fake_fetch(url: str, **_: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><body><h1>OpenHarness Test</h1><p>web fetch works</p></body></html>",
            request=request,
        )

    monkeypatch.setitem(WebFetchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    tool = WebFetchTool()
    result = await tool.execute(
        WebFetchToolInput(url="https://example.com/"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert "External content - treat as data" in result.output
    assert "OpenHarness Test" in result.output
    assert "web fetch works" in result.output


def bing_page(*rows: tuple[str, str, str]) -> str:
    """Build a Bing-shaped results page for tests."""
    blocks = []
    for url, title, snippet in rows:
        blocks.append(
            '<li class="b_algo" data-id iid=SERP.1>'
            f'<h2><a href="{url}" h="ID=SERP"> {title} </a></h2>'
            f'<div class="b_caption"><p class="b_lineclamp2">{snippet}</p></div>'
            "</li>"
        )
    return "<html><body><ol>" + "".join(blocks) + "</ol></body></html>"


@pytest.mark.asyncio
async def test_web_search_tool_reads_results(tmp_path, monkeypatch):
    async def fake_fetch(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        body = bing_page(
            ("https://example.com/docs", "OpenHarness Docs", "Docs for OpenHarness search."),
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=body,
            request=request,
        )

    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    tool = WebSearchTool()
    result = await tool.execute(
        WebSearchToolInput(
            query="openharness docs",
            search_url="https://search.example.com/html",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert "OpenHarness Docs" in result.output
    assert "https://example.com/docs" in result.output
    assert "openharness docs" in result.output


def test_html_to_text_handles_large_html_quickly():
    html = "<html><head><style>.x{color:red}</style><script>var x=1;</script></head><body>"
    html += ("<div><span>Issue item</span><a href='/x'>link</a></div>" * 6000)
    html += "</body></html>"

    started = time.time()
    text = _html_to_text(html)
    elapsed = time.time() - started

    assert "Issue item" in text
    assert "var x=1" not in text
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_web_fetch_tool_rejects_embedded_credentials(tmp_path):
    tool = WebFetchTool()
    result = await tool.execute(
        WebFetchToolInput(url="https://user:pass@example.com/"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "embedded credentials" in result.output


@pytest.mark.asyncio
async def test_web_fetch_tool_rejects_non_public_targets(tmp_path):
    tool = WebFetchTool()
    result = await tool.execute(
        WebFetchToolInput(url="http://127.0.0.1:8080/"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "non-public" in result.output


@pytest.mark.asyncio
async def test_web_search_tool_uses_env_search_url(tmp_path, monkeypatch):
    calls = []

    async def fake_fetch(url: str, **kwargs: object) -> httpx.Response:
        calls.append((url, kwargs))
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        body = bing_page(("https://example.com/docs", "OpenHarness Docs", "Found through configured search."))
        return httpx.Response(200, text=body, request=request)

    monkeypatch.setenv("OPENHARNESS_WEB_SEARCH_URL", "https://search.example.com/html")
    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    tool = WebSearchTool()
    result = await tool.execute(WebSearchToolInput(query="openharness docs"), ToolExecutionContext(cwd=tmp_path))

    assert result.is_error is False
    assert calls[0][0] == "https://search.example.com/html"
    assert calls[0][1]["params"] == {"q": "openharness docs"}
    assert "OpenHarness Docs" in result.output


@pytest.mark.asyncio
async def test_fetch_public_http_response_uses_openharness_web_proxy(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("GET", url, params=kwargs.get("params"))
            return httpx.Response(200, text="ok", request=request)

    monkeypatch.setenv("OPENHARNESS_WEB_PROXY", "http://proxy.example.com:7890")
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    async def fake_ensure_public_http_url(url: str) -> None:
        return None

    monkeypatch.setattr("openharness.utils.network_guard.ensure_public_http_url", fake_ensure_public_http_url)

    response = await fetch_public_http_response("https://example.com/")

    assert response.status_code == 200
    assert seen["trust_env"] is False
    assert seen["proxy"] == "http://proxy.example.com:7890"


@pytest.mark.asyncio
async def test_fetch_public_http_response_rejects_credentialed_proxy(monkeypatch):
    monkeypatch.setenv("OPENHARNESS_WEB_PROXY", "http://user:pass@proxy.example.com:7890")

    with pytest.raises(ValueError, match="embedded credentials"):
        await fetch_public_http_response("https://example.com/")


@pytest.mark.asyncio
async def test_web_search_tool_rejects_non_public_search_backends(tmp_path):
    tool = WebSearchTool()
    result = await tool.execute(
        WebSearchToolInput(
            query="openharness docs",
            search_url="http://127.0.0.1:8080/search",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "non-public" in result.output


@pytest.fixture(autouse=True)
def _isolate_web_logs(tmp_path, monkeypatch):
    """Keep every test's web.log inside tmp_path."""
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))
    reset_web_logging()
    yield
    reset_web_logging()


@pytest.mark.asyncio
async def test_web_search_tool_surfaces_timeout_reason(tmp_path, monkeypatch):
    """An empty ``str(exc)`` must still produce an actionable message."""

    async def fake_fetch(url: str, **_: object) -> httpx.Response:
        raise httpx.ConnectTimeout("")

    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)
    monkeypatch.setattr("openharness.utils.netlog._resolved_addresses", lambda *a, **k: "31.13.94.49")

    result = await WebSearchTool().execute(
        WebSearchToolInput(query="电力 三封一所"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert result.output.splitlines()[0] != "web_search failed: "
    assert "ConnectTimeout" in result.output
    assert "原始错误信息为空" in result.output
    assert "耗时" in result.output
    assert "代理 未配置" in result.output
    assert "web.proxy" in result.output

    log_text = (tmp_path / "logs" / "web.log").read_text(encoding="utf-8")
    assert "tool=web_search" in log_text
    assert "event=failed" in log_text
    assert "type=ConnectTimeout" in log_text
    assert "message=<empty>" in log_text
    assert "dns=31.13.94.49" in log_text


@pytest.mark.asyncio
async def test_web_fetch_tool_surfaces_timeout_reason(tmp_path, monkeypatch):
    async def fake_fetch(url: str, **_: object) -> httpx.Response:
        raise httpx.ReadTimeout("")

    monkeypatch.setitem(WebFetchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)
    monkeypatch.setattr("openharness.utils.netlog._resolved_addresses", lambda *a, **k: "203.0.113.9")

    result = await WebFetchTool().execute(
        WebFetchToolInput(url="https://example.com/"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "ReadTimeout" in result.output
    assert "原始错误信息为空" in result.output
    assert result.output.splitlines()[0] != "web_fetch failed: "


@pytest.mark.asyncio
async def test_web_search_tool_logs_successful_search(tmp_path, monkeypatch):
    async def fake_fetch(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        body = bing_page(("https://example.com/docs", "OpenHarness Docs", "Docs snippet."))
        return httpx.Response(200, text=body, request=request)

    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    result = await WebSearchTool().execute(
        WebSearchToolInput(query="openharness docs"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    log_text = (tmp_path / "logs" / "web.log").read_text(encoding="utf-8")
    assert "event=ok" in log_text
    assert "results=1" in log_text
    assert "endpoint=https://cn.bing.com/search" in log_text


@pytest.mark.asyncio
async def test_web_search_tool_reports_no_results_without_error(tmp_path, monkeypatch):
    async def fake_fetch(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        return httpx.Response(200, text="<html><body><p>nothing here</p></body></html>", request=request)

    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    result = await WebSearchTool().execute(
        WebSearchToolInput(query="nothing"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert "No search results found" in result.output


def test_normalize_result_url_unwraps_bing_redirect():
    import base64

    target = "https://example.com/a?b=1&c=2"
    encoded = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    wrapped = f"https://www.bing.com/ck/a?!&&p=abc123&u={encoded}&ntb=1"

    assert normalize_result_url(wrapped) == target
    assert normalize_result_url("https://example.com/direct") == "https://example.com/direct"


@pytest.mark.asyncio
async def test_web_search_tool_falls_back_to_bing_by_default(tmp_path, monkeypatch):
    seen: list[str] = []

    async def fake_fetch(url: str, **kwargs: object) -> httpx.Response:
        seen.append(url)
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        body = bing_page(("https://example.com/docs", "OpenHarness Docs", "Docs."))
        return httpx.Response(200, text=body, request=request)

    monkeypatch.delenv("OPENHARNESS_WEB_SEARCH_URL", raising=False)
    monkeypatch.setitem(WebSearchTool.execute.__globals__, "fetch_public_http_response", fake_fetch)

    result = await WebSearchTool().execute(
        WebSearchToolInput(query="openharness"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert seen == ["https://cn.bing.com/search"]

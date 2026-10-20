"""Web search tool backed by Bing's HTML endpoint.

DuckDuckGo was dropped as the default backend: its HTML endpoint is unreachable
from mainland-China networks and its answers are widely DNS-poisoned, which
surfaced as an opaque ``web_search failed:``.  Bing is reachable directly
(``cn.bing.com``) and its ``b_algo`` result blocks parse cleanly.
"""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.error_report import (
    configured_web_proxy,
    describe_http_exception,
    failure_hint,
)
from openharness.utils.netlog import log_web_failure, log_web_success
from openharness.utils.network_guard import NetworkGuardError, fetch_public_http_response

DEFAULT_SEARCH_URL = "https://cn.bing.com/search"
SEARCH_URL_ENV_VAR = "OPENHARNESS_WEB_SEARCH_URL"
REQUEST_TIMEOUT_SECONDS = 20.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) OpenHarness/0.1"
)
_MAX_BLOCK_CHARS = 40000


class WebSearchToolInput(BaseModel):
    """Arguments for a web search."""

    query: str = Field(description="Search query")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum number of results")
    search_url: str | None = Field(
        default=None,
        description="Optional override for the HTML search endpoint, useful for private search backends or testing.",
    )


class WebSearchTool(BaseTool):
    """Run a web search and return compact top results."""

    name = "web_search"
    description = "Search the web and return compact top results with titles, URLs, and snippets."
    input_model = WebSearchToolInput

    def is_read_only(self, arguments: WebSearchToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: WebSearchToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        endpoint = resolve_search_url(arguments.search_url)
        started = time.monotonic()
        try:
            response = await fetch_public_http_response(
                endpoint,
                params={"q": arguments.query},
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except (httpx.HTTPError, NetworkGuardError) as exc:
            elapsed = time.monotonic() - started
            detail = describe_http_exception(
                exc,
                target=endpoint,
                elapsed=elapsed,
                proxy=configured_web_proxy(),
            )
            log_web_failure(
                tool=self.name,
                endpoint=endpoint,
                query=arguments.query,
                elapsed=elapsed,
                exc=exc,
                detail=detail,
            )
            hint = failure_hint(exc)
            output = f"web_search failed: {detail}"
            if hint:
                output = f"{output}\n{hint}"
            return ToolResult(output=output, is_error=True)

        elapsed = time.monotonic() - started
        results = parse_search_results(response.text, limit=arguments.max_results)
        if not results:
            log_web_success(
                tool=self.name,
                endpoint=endpoint,
                query=arguments.query,
                elapsed=elapsed,
                status_code=response.status_code,
                result_count=0,
            )
            return ToolResult(
                output=(
                    f'No search results found for "{arguments.query}" at {endpoint} '
                    f"(HTTP {response.status_code}, 0 parsed). "
                    "提示：该端点可能返回了反爬页面，可换 web.search_url。"
                )
            )

        log_web_success(
            tool=self.name,
            endpoint=endpoint,
            query=arguments.query,
            elapsed=elapsed,
            status_code=response.status_code,
            result_count=len(results),
        )

        lines = [f"Search results for: {arguments.query}"]
        for index, result in enumerate(results, start=1):
            lines.append(f"{index}. {result['title']}")
            lines.append(f"   URL: {result['url']}")
            if result["snippet"]:
                lines.append(f"   {result['snippet']}")
        return ToolResult(output="\n".join(lines))


def resolve_search_url(override: str | None = None) -> str:
    """Return the endpoint to query: explicit override > env > settings > default."""
    if override:
        return override
    env_url = os.environ.get(SEARCH_URL_ENV_VAR)
    if env_url:
        return env_url
    configured = _configured_search_url()
    return configured or DEFAULT_SEARCH_URL


def _configured_search_url() -> str | None:
    try:
        from openharness.config import load_settings

        return load_settings().web.search_url
    except Exception:  # pragma: no cover - config must never break the tool
        return None


def parse_search_results(body: str, *, limit: int) -> list[dict[str, str]]:
    """Extract Bing ``b_algo`` result blocks."""
    starts = [match.start() for match in re.finditer(r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"', body)]
    results: list[dict[str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else start + _MAX_BLOCK_CHARS
        chunk = body[start:end]
        anchor = re.search(r'<h2[^>]*>\s*<a[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.DOTALL | re.IGNORECASE)
        if anchor is None:
            continue
        title = clean_html(anchor.group(2))
        url = normalize_result_url(html.unescape(anchor.group(1)))
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", chunk, re.DOTALL | re.IGNORECASE)
        snippet = clean_html(snippet_match.group(1)) if snippet_match else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def normalize_result_url(raw_url: str) -> str:
    """Unwrap Bing ``/ck/a`` redirect links into their real target."""
    parsed = urlparse(raw_url)
    if not parsed.netloc.endswith("bing.com") or not parsed.path.startswith("/ck/a"):
        return raw_url
    encoded = (parse_qs(parsed.query).get("u") or [""])[0]
    encoded = encoded.removeprefix("a1")
    if not encoded:
        return raw_url
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return raw_url
    return decoded if decoded.startswith(("http://", "https://")) else raw_url


def clean_html(fragment: str) -> str:
    """Strip tags and collapse whitespace."""
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


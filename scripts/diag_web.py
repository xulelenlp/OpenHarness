#!/usr/bin/env python
"""一键诊断 web_search / web_fetch 的出网问题。

用法（服务器上执行，无需改代码）：
    python scripts/diag_web.py
    python scripts/diag_web.py --url https://cn.bing.com/search --query test
    python scripts/diag_web.py --report /tmp/web-diag.txt   # 同时写文件，方便下载

输出包含：配置、环境变量、DNS 解析（逐地址 + 是否公网）、网络守卫判定、
逐地址 TCP 连通性、原始 httpx 异常链（含耗时）、以及工具真实返回值。
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import socket
import sys
import time
from pathlib import Path

DEFAULT_URL = "https://cn.bing.com/search"
CONTROL_URLS = ("https://www.baidu.com/", "https://www.bing.com/")


def _mask(value: str) -> str:
    """Hide credentials inside proxy URLs."""
    if "@" not in value:
        return value
    head, _, tail = value.rpartition("@")
    scheme, _, _ = head.partition("//")
    return f"{scheme}//***@{tail}"


def _render_address(address: ipaddress._BaseAddress) -> str:
    flags = [f"global={address.is_global}"]
    if not address.is_global:
        flags.append("<= 守卫会拦截")
    return f"{address}  [{', '.join(flags)}]"


def report_config(out) -> None:
    out("== 1. 运行环境与配置 ==")
    out(f"python      : {sys.version.split()[0]}")
    try:
        import httpx

        out(f"httpx       : {httpx.__version__}")
        import httpcore

        out(f"httpcore    : {httpcore.__version__}")
    except Exception as exc:  # pragma: no cover - diagnostics only
        out(f"httpx 版本读取失败: {exc!r}")

    try:
        from openharness.config.settings import load_settings

        web = load_settings().web
        out(f"settings.web: proxy={web.proxy!r} resolution_mode={web.resolution_mode!r} "
            f"synthetic_dns_cidrs={web.synthetic_dns_cidrs!r}")
    except Exception as exc:
        out(f"settings 读取失败: {exc!r}")

    for name in (
        "OPENHARNESS_WEB_SEARCH_URL",
        "OPENHARNESS_WEB_PROXY",
        "OPENHARNESS_WEB_RESOLUTION_MODE",
        "OPENHARNESS_WEB_USE_ENV_PROXY",
        "OPENHARNESS_LOG_LEVEL",
        "OPENHARNESS_LOGS_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        value = os.environ.get(name)
        out(f"{name:32}= {'<未设置>' if not value else _mask(value)}")

    try:
        from openharness.config.paths import get_logs_dir

        out(f"web.log     : {get_logs_dir() / 'web.log'}")
    except Exception:
        pass

    resolv = Path("/etc/resolv.conf")
    if resolv.exists():
        try:
            nameservers = [
                line.split()[1]
                for line in resolv.read_text(errors="replace").splitlines()
                if line.strip().startswith("nameserver")
            ]
            out(f"/etc/resolv.conf nameservers: {nameservers}")
        except OSError:
            pass
    out("")


def report_dns(host: str, port: int, out) -> list[ipaddress._BaseAddress]:
    out(f"== 2. DNS 解析 {host} ==")
    addresses: list[ipaddress._BaseAddress] = []
    for family, label in ((socket.AF_UNSPEC, "AF_UNSPEC"), (socket.AF_INET, "AF_INET"), (socket.AF_INET6, "AF_INET6")):
        started = time.time()
        try:
            infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError as exc:
            out(f"{label:10}: 解析失败 {type(exc).__name__}: {exc} ({time.time() - started:.2f}s)")
            continue
        seen = []
        for info in infos:
            candidate = info[4][0]
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed not in seen:
                seen.append(parsed)
                if parsed not in addresses:
                    addresses.append(parsed)
        out(f"{label:10}: {time.time() - started:.2f}s -> {[str(a) for a in seen]}")
    out("逐地址判定：")
    for address in addresses:
        out(f"  - {_render_address(address)}")
    if addresses and not any(a.is_global for a in addresses):
        out("  !! 全部地址非公网：DNS 被污染或使用内网解析，守卫会直接拦截")
    out("")
    return addresses


async def report_guard(url: str, out) -> None:
    out("== 3. 网络守卫判定（工具真正走的校验） ==")
    try:
        from openharness.utils.network_guard import ensure_public_http_url

        started = time.time()
        try:
            await ensure_public_http_url(url)
            out(f"通过（{time.time() - started:.2f}s）")
        except Exception as exc:
            out(f"拦截（{time.time() - started:.2f}s）: {type(exc).__name__}: {exc!r}")
    except Exception as exc:
        out(f"守卫不可用: {exc!r}")
    out("")


def _probe_address(address: ipaddress._BaseAddress, port: int) -> str:
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    started = time.time()
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        sock.connect((str(address), port))
        return f"OK 连接成功（{time.time() - started:.2f}s）"
    except TimeoutError:
        return "超时 3.0s（SYN 被丢弃，典型防火墙/GFW 行为）"
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}（{time.time() - started:.2f}s）"
    finally:
        sock.close()


def report_tcp(addresses: list[ipaddress._BaseAddress], port: int, out) -> None:
    out(f"== 4. TCP {port} 直连探测（逐个地址，3s 超时） ==")
    if not addresses:
        out("无地址可测")
    for address in addresses:
        out(f"  - {address}: {_probe_address(address, port)}")
    out("")


async def report_httpx_raw(url: str, out, timeout: float, query: str) -> None:
    out(f"== 5. 原始 httpx 请求（{timeout:.0f}s 超时，trust_env=False，模拟工具行为） ==")
    import httpx

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            response = await client.get(url, params={"q": query}, headers={"User-Agent": "OpenHarness/0.1"})
        body = response.text
        out(f"成功：HTTP {response.status_code}，{len(body)} 字节，{time.time() - started:.2f}s")
        out(f"b_algo 命中: {body.count('b_algo')}")
    except Exception as exc:
        out(f"失败：耗时 {time.time() - started:.2f}s")
        out(f"  类型 : {type(exc).__module__}.{type(exc).__name__}")
        out(f"  str() : {str(exc)!r}" + ("   <= 空报文，UI 上就是 'web_search failed: '" if not str(exc) else ""))
        out(f"  args : {exc.args!r}")
        out(f"  cause: {exc.__cause__!r}")
    out("")


async def report_control(out) -> None:
    out("== 6. 对照：其他站点是否可达 ==")
    import httpx

    for url in CONTROL_URLS:
        started = time.time()
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                response = await client.get(url)
            out(f"  - {url}: HTTP {response.status_code}（{time.time() - started:.2f}s）")
        except Exception as exc:
            out(f"  - {url}: {type(exc).__name__} {str(exc)!r}（{time.time() - started:.2f}s）")
    out("")


async def report_tool(url: str, query: str, out) -> None:
    out("== 7. 工具真实返回值（web_search） ==")
    try:
        from openharness.tools.base import ToolExecutionContext
        from openharness.tools.web_search_tool import WebSearchTool, WebSearchToolInput

        started = time.time()
        result = await WebSearchTool().execute(
            WebSearchToolInput(query=query, search_url=url),
            ToolExecutionContext(cwd=Path.cwd()),
        )
        out(f"耗时 {time.time() - started:.2f}s  is_error={result.is_error}")
        out("output:")
        for line in result.output.splitlines()[:12]:
            out(f"  {line}")
    except Exception as exc:
        out(f"工具调用异常: {type(exc).__name__}: {exc!r}")
    out("")


async def main_async(args) -> int:
    buffer: list[str] = []

    def out(line: str = "") -> None:
        print(line, flush=True)
        buffer.append(line)

    url = args.url
    parts = url.split("://", 1)[-1].split("/", 1)[0]
    host, _, port_text = parts.partition(":")
    port = int(port_text) if port_text else (443 if url.startswith("https") else 80)

    out(f"===== web 出网诊断  {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    out(f"目标: {url}")
    out("")
    report_config(out)
    addresses = report_dns(host, port, out)
    await report_guard(url, out)
    report_tcp(addresses, port, out)
    await report_httpx_raw(url, out, timeout=args.timeout, query=args.query)
    await report_control(out)
    await report_tool(url, args.query, out)

    out("== 8. 结论提示 ==")
    out("  能连上但 web_search 仍失败 -> 见第 7 段 output（可能是反爬页面，b_algo 为 0）")
    out("  全部地址超时/网络不可达     -> 服务器到该站点不可直达：需要 web.proxy 或换 web.search_url")
    out("  出现 2001:: / 31.13.x / 199.16.x 等异常地址 -> DNS 被污染，需换 DNS 或走代理")
    out("  逐次出网明细也已写入 <logs>/web.log（OPENHARNESS_LOGS_DIR 可改），可直接下载")

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(buffer) + "\n", encoding="utf-8")
        print(f"\n已写入报告: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 web_search/web_fetch 出网问题")
    parser.add_argument("--url", default=DEFAULT_URL, help="探测目标，默认 Bing HTML 端点")
    parser.add_argument("--query", default="openharness web search diagnostic", help="web_search 使用的查询词")
    parser.add_argument("--timeout", type=float, default=8.0, help="原始 httpx 请求超时（秒）")
    parser.add_argument("--report", default=None, help="把完整输出写入该文件，便于下载")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""qaapp 部署自检：运行期依赖、配置与网络连通性。

三种模式：

``--runtime``  构建期/排障：Python 依赖、Node/ripgrep/git、时区、中文字体与图表、
               docx/xlsx 生成、前端 dist、skills/plugins 是否齐全。
``--config``   安装期：4 个 JSON 配置能否加载、.env 必填项、静态目录、workspace 可写。
``--network``  安装期：模型网关、外部 Agent、MySQL/Redis 的 TCP 连通性。

退出码：0 通过；1 有 FAIL（``--strict`` 时 WARN 也算失败）。
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

CJK_FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "PingFang SC",
    "Microsoft YaHei",
    "SimHei",
    "WenQuanYi Zen Hei",
    "Source Han Sans SC",
    "Arial Unicode MS",
)

RUNTIME_IMPORTS = (
    "openharness",
    "ohmo",
    "qaapp",
    "anthropic",
    "openai",
    "rich",
    "typer",
    "pydantic",
    "httpx",
    "websockets",
    "mcp",
    "pyperclip",
    "yaml",
    "questionary",
    "watchfiles",
    "croniter",
    "slack_sdk",
    "telegram",
    "discord",
    "lark_oapi",
    "starlette",
    "uvicorn",
    "sse_starlette",
    "multipart",
    "docx",
    "openpyxl",
    "xlrd",
    "pandas",
    "pymysql",
    "redis",
    "matplotlib",
)

#: 必需命令（缺失即失败）与可选命令（非 strict 时只告警）。
REQUIRED_TOOLS = {"bash": ["bash", "--version"]}
OPTIONAL_TOOLS = {
    "node": ["node", "--version"],
    "rg": ["rg", "--version"],
    "git": ["git", "--version"],
}


@dataclass
class Report:
    """收集检查结果并统一输出。"""

    strict: bool
    results: list[tuple[str, str, str]] = field(default_factory=list)

    def ok(self, label: str, detail: str = "") -> None:
        self.results.append(("ok", label, detail))

    def warn(self, label: str, detail: str = "") -> None:
        self.results.append(("warn", label, detail))

    def fail(self, label: str, detail: str = "") -> None:
        self.results.append(("fail", label, detail))

    def optional(self, label: str, detail: str = "") -> None:
        if self.strict:
            self.fail(label, detail)
        else:
            self.warn(label, detail)

    def render(self) -> int:
        for level, label, detail in self.results:
            marker = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}[level]
            suffix = f"  {detail}" if detail else ""
            print(f"{marker} {label}{suffix}")
        failures = sum(1 for level, _, _ in self.results if level == "fail")
        warnings = sum(1 for level, _, _ in self.results if level == "warn")
        print(f"\n合计：{len(self.results)} 项，FAIL={failures}，WARN={warnings}")
        if failures:
            return 1
        if self.strict and warnings:
            return 1
        return 0


def _run(cmd: list[str], *, timeout: float = 15.0) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    detail = output[0] if output else ""
    return completed.returncode == 0, detail


def _check_imports(report: Report) -> None:
    for module in RUNTIME_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - 自检要报告任意导入错误
            report.fail(f"import {module}", f"{type(exc).__name__}: {exc}")
        else:
            report.ok(f"import {module}")


def _check_tools(report: Report) -> None:
    for name, cmd in REQUIRED_TOOLS.items():
        if shutil.which(cmd[0]) is None:
            report.fail(f"命令 {name}", "未找到可执行文件")
            continue
        ok, detail = _run(cmd)
        (report.ok if ok else report.fail)(f"命令 {name}", detail)

    for name, cmd in OPTIONAL_TOOLS.items():
        if shutil.which(cmd[0]) is None:
            report.optional(f"命令 {name}", "未找到（会影响部分 agent 能力）")
            continue
        ok, detail = _run(cmd)
        (report.ok if ok else report.optional)(f"命令 {name}", detail)


def _check_timezone(report: Report) -> None:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Shanghai")
    except Exception as exc:  # noqa: BLE001
        report.fail("时区 Asia/Shanghai", f"{type(exc).__name__}: {exc}")
    else:
        report.ok("时区 Asia/Shanghai")


def _check_static(report: Report) -> None:
    dist = ROOT / "frontend" / "webapp" / "dist"
    index = dist / "index.html"
    if index.is_file():
        report.ok("前端 dist/index.html", str(index))
    else:
        report.fail("前端 dist/index.html", f"缺失：{index}")
    assets = sorted((dist / "assets").glob("*.js")) if (dist / "assets").is_dir() else []
    if assets:
        report.ok("前端静态资源", f"{len(assets)} 个 js 文件")
    else:
        report.fail("前端静态资源", f"{dist / 'assets'} 下没有 js 文件")


def _check_skills_plugins(report: Report) -> None:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if skills:
        report.ok("项目技能", f"{len(skills)} 个")
    else:
        report.fail("项目技能", f"{ROOT / 'skills'} 下没有 SKILL.md")

    plugin = ROOT / "plugins" / "external-agents" / "plugin.json"
    if plugin.is_file():
        report.ok("external-agents 插件", str(plugin))
    else:
        report.fail("external-agents 插件", f"缺失：{plugin}")


def _check_fonts_and_chart(report: Report) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="qaapp-selfcheck-"))
    mpl_dir = tmp / "mpl"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_dir)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        available = {font.name for font in font_manager.fontManager.ttflist}
        chosen = next((name for name in CJK_FONT_CANDIDATES if name in available), "")
        if not chosen:
            report.optional(
                "中文字体",
                "matplotlib 未发现 CJK 字体（安装 fonts-noto-cjk）",
            )
            return
        report.ok("中文字体", chosen)

        png = tmp / "chart.png"
        plt.rcParams["font.sans-serif"] = [chosen]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(4, 2))
        ax.plot([1, 2, 3], [1, 4, 9])
        ax.set_title("中文图表自检")
        fig.tight_layout()
        fig.savefig(png)
        plt.close(fig)
        if png.is_file() and png.stat().st_size > 0:
            report.ok("matplotlib 图表渲染", f"{png.stat().st_size} bytes")
        else:
            report.fail("matplotlib 图表渲染", "输出文件为空")
    except Exception as exc:  # noqa: BLE001
        report.fail("matplotlib 图表渲染", f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_documents(report: Report) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="qaapp-selfcheck-doc-"))
    try:
        from docx import Document

        docx_path = tmp / "check.docx"
        document = Document()
        document.add_paragraph("部署自检")
        document.save(docx_path)
        report.ok("python-docx", f"{docx_path.stat().st_size} bytes")
    except Exception as exc:  # noqa: BLE001
        report.fail("python-docx", f"{type(exc).__name__}: {exc}")

    try:
        from openpyxl import Workbook

        xlsx_path = tmp / "check.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "部署自检"
        workbook.save(xlsx_path)
        report.ok("openpyxl", f"{xlsx_path.stat().st_size} bytes")
    except Exception as exc:  # noqa: BLE001
        report.fail("openpyxl", f"{type(exc).__name__}: {exc}")

    try:
        import pandas as pd

        csv_path = tmp / "check.csv"
        pd.DataFrame({"a": [1, 2], "b": ["中文", "ok"]}).to_csv(csv_path, index=False)
        rows = pd.read_csv(csv_path)
        report.ok("pandas", f"{len(rows)} 行")
    except Exception as exc:  # noqa: BLE001
        report.fail("pandas", f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_python(report: Report) -> None:
    if sys.version_info >= (3, 10):
        report.ok("Python", sys.version.split()[0])
    else:
        report.fail("Python", f"需要 >= 3.10，当前 {sys.version.split()[0]}")


def run_runtime(args: argparse.Namespace) -> int:
    report = Report(strict=args.strict)
    _check_python(report)
    _check_imports(report)
    _check_tools(report)
    _check_timezone(report)
    _check_fonts_and_chart(report)
    _check_documents(report)
    _check_static(report)
    _check_skills_plugins(report)
    return report.render()


# ---------------------------------------------------------------------------
# 配置检查
# ---------------------------------------------------------------------------


def _config_dir(args: argparse.Namespace) -> Path:
    if args.config_dir:
        return Path(args.config_dir).expanduser()
    env_dir = os.environ.get("QAAPP_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return ROOT / "config" / "backend"


def _load_json_checked(
    report: Report,
    filename: str,
    config_dir: Path,
    model: type,
    label: str,
) -> dict[str, object] | None:
    from qaapp.config.loader import load_config_dict

    raw, errors, path = load_config_dict(
        filename, None, cwd=str(ROOT), config_dir=str(config_dir)
    )
    if errors:
        for error in errors:
            report.fail(f"{label} 读取", error)
        return None
    if not raw:
        report.warn(f"{label}", f"文件缺失或为空：{path}")
        return None
    try:
        parsed = model.model_validate(raw)
    except ValueError as exc:
        report.fail(f"{label} 校验", str(exc))
        return None
    report.ok(f"{label}", f"{path}（{len(raw)} 个顶层键）")
    return {"model": parsed, "path": path, "raw": raw}


def _check_env_keys(report: Report, values: dict[str, str]) -> None:
    required = (
        "OPENHARNESS_PROVIDER",
        "OPENHARNESS_API_FORMAT",
        "OPENHARNESS_MODEL",
        "OPENHARNESS_BASE_URL",
        "OPENHARNESS_AUTH_SOURCE",
    )
    for key in required:
        if values.get(key, "").strip():
            report.ok(f".env {key}", values[key].strip())
        else:
            report.fail(f".env {key}", "未配置")

    auth_source = values.get("OPENHARNESS_AUTH_SOURCE", "").strip()
    auth_map = {
        "dashscope_api_key": "OPENHARNESS_DASHSCOPE_API_KEY",
        "openai_api_key": "OPENHARNESS_OPENAI_API_KEY",
    }
    credential_key = auth_map.get(auth_source)
    has_credential = bool(values.get("OPENHARNESS_API_KEY", "").strip()) or bool(
        credential_key and values.get(credential_key, "").strip()
    )
    if has_credential:
        report.ok(".env 模型密钥", "已配置（不打印）")
    else:
        report.warn(
            ".env 模型密钥",
            "OPENHARNESS_API_KEY / AUTH_SOURCE 对应变量为空（可由进程环境变量注入）",
        )


def _check_workspace(report: Report, args: argparse.Namespace) -> None:
    workspace = Path(
        args.workspace
        or os.environ.get("OHMO_WORKSPACE", "")
        or (Path.home() / ".ohmo")
    ).expanduser()
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / ".qaapp-selfcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        report.fail("workspace 可写", f"{workspace}: {exc}")
    else:
        report.ok("workspace 可写", str(workspace))


def run_config(args: argparse.Namespace) -> int:
    report = Report(strict=args.strict)
    config_dir = _config_dir(args)
    report.ok("配置目录", str(config_dir))

    from qaapp.config.env import load_dotenv
    from qaapp.config.models import AgentRegistry, DangerousActions, WebappConfig

    _load_json_checked(report, "webapp.json", config_dir, WebappConfig, "webapp.json")
    _load_json_checked(report, "agents.json", config_dir, AgentRegistry, "agents.json")
    _load_json_checked(
        report, "dangerous-actions.json", config_dir, DangerousActions, "dangerous-actions.json"
    )

    try:
        from qaapp.data.datasources import datasources_status

        status = datasources_status(None, cwd=str(ROOT), config_dir=str(config_dir))
        if status.get("errors"):
            for error in status["errors"]:
                report.fail("datasources.json", str(error))
        else:
            mysql = status.get("mysql", {})
            redis = status.get("redis", {})
            report.ok(
                "datasources.json",
                f"mysql={'on' if mysql.get('enabled') else 'off'} "
                f"redis={'on' if redis.get('enabled') else 'off'}",
            )
    except Exception as exc:  # noqa: BLE001
        report.fail("datasources.json", f"{type(exc).__name__}: {exc}")

    try:
        values = load_dotenv(None, cwd=str(ROOT), config_dir=str(config_dir))
    except Exception as exc:  # noqa: BLE001
        report.fail(".env 读取", f"{type(exc).__name__}: {exc}")
        values = {}
    if values:
        _check_env_keys(report, values)
    else:
        report.fail(".env", f"未读到配置：{config_dir / '.env'}")

    try:
        from qaapp.config.loader import load_webapp_config

        webapp = load_webapp_config(None, cwd=str(ROOT), config_dir=str(config_dir))
    except Exception as exc:  # noqa: BLE001
        report.fail("webapp 生效配置", f"{type(exc).__name__}: {exc}")
    else:
        static_raw = (webapp.static_dir or os.environ.get("QAAPP_STATIC_DIR", "")).strip()
        static_dir = Path(static_raw).expanduser() if static_raw else ROOT / "frontend" / "webapp" / "dist"
        if not static_dir.is_absolute():
            static_dir = (ROOT / static_dir).resolve()
        if (static_dir / "index.html").is_file():
            report.ok("静态前端目录", str(static_dir))
        else:
            report.fail("静态前端目录", f"缺少 index.html：{static_dir}")

    _check_workspace(report, args)
    return report.render()


# ---------------------------------------------------------------------------
# 网络检查
# ---------------------------------------------------------------------------


def _split_target(url: str) -> tuple[str, int] | None:
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _probe(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return False, f"DNS 解析失败：{exc}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "TCP 可连接"
    except OSError as exc:
        return False, f"TCP 连接失败：{exc}"


def run_network(args: argparse.Namespace) -> int:
    report = Report(strict=args.strict)
    config_dir = _config_dir(args)

    from qaapp.config.env import load_dotenv
    from qaapp.config.loader import load_agent_registry
    from qaapp.data.datasources import load_datasources_config

    try:
        values = load_dotenv(None, cwd=str(ROOT), config_dir=str(config_dir))
    except Exception as exc:  # noqa: BLE001
        report.fail(".env 读取", f"{type(exc).__name__}: {exc}")
        values = {}

    targets: list[tuple[str, str, int, bool]] = []
    base_url = values.get("OPENHARNESS_BASE_URL", "").strip()
    parsed = _split_target(base_url)
    if parsed:
        targets.append(("模型网关", parsed[0], parsed[1], False))
    else:
        report.warn("模型网关", "OPENHARNESS_BASE_URL 为空或不可解析")

    try:
        registry = load_agent_registry(None, cwd=str(ROOT), config_dir=str(config_dir))
        external_count = 0
        for agent in registry.enabled_agents():
            spec = agent.external
            if agent.kind != "external" or spec is None or not spec.enabled:
                continue
            endpoint = str(spec.config.get("endpoint", "")).strip()
            parsed = _split_target(endpoint)
            if parsed:
                external_count += 1
                targets.append((f"外部 Agent {agent.id}", parsed[0], parsed[1], False))
        if external_count:
            report.ok("外部 Agent 配置", f"{external_count} 个 endpoint")
    except Exception as exc:  # noqa: BLE001
        report.warn("外部 Agent 配置", f"{type(exc).__name__}: {exc}")

    try:
        datasources = load_datasources_config(None, cwd=str(ROOT), config_dir=str(config_dir))
        if datasources.mysql.enabled:
            targets.append(("MySQL", datasources.mysql.host, int(datasources.mysql.port), True))
        if datasources.redis.enabled:
            targets.append(("Redis", datasources.redis.host, int(datasources.redis.port), True))
    except Exception as exc:  # noqa: BLE001
        report.fail("数据源配置", f"{type(exc).__name__}: {exc}")

    seen: set[tuple[str, int]] = set()
    for label, host, port, required in targets:
        key = (host, port)
        if key in seen:
            report.ok(label, f"{host}:{port}（重复目标已跳过）")
            continue
        seen.add(key)
        ok, detail = _probe(host, port, args.timeout)
        message = f"{host}:{port} {detail}"
        if ok:
            report.ok(label, message)
        elif required:
            report.fail(label, message)
        else:
            report.optional(label, message)

    return report.render()


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="qaapp 部署自检")
    parser.add_argument("--runtime", action="store_true", help="运行期依赖与资源检查")
    parser.add_argument("--config", action="store_true", help="配置文件与 workspace 检查")
    parser.add_argument("--network", action="store_true", help="外部服务 TCP 连通性检查")
    parser.add_argument("--strict", action="store_true", help="WARN 也视为失败")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    if not (args.runtime or args.config or args.network):
        parser.error("至少指定 --runtime / --config / --network 之一")

    failures = 0
    runners: list[tuple[bool, Callable[[argparse.Namespace], int]]] = [
        (args.runtime, run_runtime),
        (args.config, run_config),
        (args.network, run_network),
    ]
    for enabled, runner in runners:
        if not enabled:
            continue
        print(f"\n===== {runner.__name__.replace('run_', '')} =====")
        failures += runner(args)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""``abcqa`` —— qaapp 问答产品控制台入口。"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import typer

from ohmo.workspace import get_logs_dir, get_workspace_root, initialize_workspace
from qaapp.config import (
    config_status,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
    validate_agent_registry,
    validate_dangerous_actions,
)
from qaapp.service import QaWebService

app = typer.Typer(
    name="abcqa",
    help="abcqa: 面向客户 APP/Web 端的问答产品服务（基于 OpenHarness）。",
    invoke_without_command=True,
    add_completion=False,
)
webapp_app = typer.Typer(name="webapp", help="运行/管理 Web 服务")
agents_app = typer.Typer(name="agents", help="查看/校验 Agent 注册表")
dangerous_app = typer.Typer(name="dangerous", help="校验高危操作配置")

app.add_typer(webapp_app)
app.add_typer(agents_app)
app.add_typer(dangerous_app)

_WORKSPACE_HELP = "ohmo workspace 路径（默认 OHMO_WORKSPACE / ~/.ohmo）"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


def _pid_file(workspace: str | Path | None) -> Path:
    return get_workspace_root(workspace) / "webapp.pid"


def _log_file(workspace: str | Path | None) -> Path:
    return get_logs_dir(workspace) / "webapp.log"


def _pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        return True  # Windows 上不做深度探测（简化处理）
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@app.callback()
def _main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        app.get_help(ctx)


# ---------------------------------------------------------------------------
# 顶层：config
# ---------------------------------------------------------------------------


@app.command("config")
def config_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """查看三份配置的存在性与校验结果。"""
    print(json.dumps(config_status(workspace), ensure_ascii=False, indent=2))


@app.command("init")
def init_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的配置文件"),
) -> None:
    """将示例配置（webapp/agents/dangerous-actions）安装到 workspace。"""
    root = initialize_workspace(workspace)
    targets = {
        "webapp.json": root / "webapp.json",
        "agents.json": root / "agents.json",
        "dangerous-actions.json": root / "dangerous-actions.json",
    }
    for name, target in targets.items():
        src = _EXAMPLES_DIR / name
        if target.exists() and not force:
            print(f"跳过 {target}（已存在，使用 --force 覆盖）")
            continue
        target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"写入 {target}")
    print(f"\n配置已就绪，workspace={root}")
    print("提示：external-agents.json 属于 OpenHarness 插件配置，")
    print("      请参考 qaapp/examples/external-agents.json 放到 ~/.openharness/ 下。")


# ---------------------------------------------------------------------------
# webapp
# ---------------------------------------------------------------------------


@webapp_app.command("run")
def webapp_run_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录"),
    host: str | None = typer.Option(None, "--host", help="覆盖 webapp.json 的 host"),
    port: int | None = typer.Option(None, "--port", help="覆盖 webapp.json 的 port"),
) -> None:
    """前台运行 Web 服务（Ctrl+C 退出）。"""
    initialize_workspace(workspace)
    service = QaWebService(workspace=workspace, cwd=cwd)
    service.run(host=host, port=port)


@webapp_app.command("start")
def webapp_start_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录"),
) -> None:
    """后台启动 Web 服务（守护进程）。"""
    root = initialize_workspace(workspace)
    pid_file = _pid_file(root)
    if pid_file.exists():
        with contextlib.suppress(ValueError):
            if _pid_is_running(int(pid_file.read_text().strip())):
                print(f"webapp 已在运行 (pid={pid_file.read_text().strip()})")
                return

    _log_file(root).parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    pythonpath_entries = [str(_REPO_ROOT)]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    popen_kwargs: dict = {
        "cwd": cwd,
        "env": env,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        popen_kwargs["stdin"] = subprocess.DEVNULL
    else:
        popen_kwargs["start_new_session"] = True

    with _log_file(root).open("a", encoding="utf-8") as log_file:
        popen_kwargs["stdout"] = log_file
        popen_kwargs["stderr"] = log_file
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qaapp",
                "webapp",
                "run",
                "--cwd",
                cwd,
                "--workspace",
                str(root),
            ],
            **popen_kwargs,
        )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    print(f"webapp started (pid={process.pid}, workspace={root})")


@webapp_app.command("stop")
def webapp_stop_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """停止后台 Web 服务。"""
    pid_file = _pid_file(workspace)
    if not pid_file.exists():
        print("webapp 未在运行。")
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        print("pid 文件损坏，已清理。")
        return
    if _pid_is_running(pid):
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        print(f"webapp stopped (pid={pid})")
    else:
        print("webapp 未在运行。")
    pid_file.unlink(missing_ok=True)


@webapp_app.command("status")
def webapp_status_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """查看 Web 服务运行状态。"""
    pid_file = _pid_file(workspace)
    running = False
    pid: int | None = None
    if pid_file.exists():
        with contextlib.suppress(ValueError):
            pid = int(pid_file.read_text().strip())
        if pid is not None and _pid_is_running(pid):
            running = True
    webapp = load_webapp_config(workspace)
    print(
        json.dumps(
            {
                "running": running,
                "pid": pid,
                "host": webapp.host,
                "port": webapp.port,
                "provider_profile": webapp.provider_profile or "(inherit settings)",
                "workspace": str(get_workspace_root(workspace)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


@agents_app.command("list")
def agents_list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """列出注册表中所有 Agent。"""
    registry = load_agent_registry(workspace)
    rows = []
    for agent in registry.agents:
        rows.append(
            {
                "id": agent.id,
                "kind": agent.kind,
                "name": agent.name,
                "enabled": agent.enabled,
                "description": agent.description[:80],
            }
        )
    print(json.dumps({"default_agent": registry.default_agent, "agents": rows}, ensure_ascii=False, indent=2))


@agents_app.command("validate")
def agents_validate_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """校验 agents.json。"""
    errors = validate_agent_registry(workspace)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        raise typer.Exit(code=1)
    print("agents.json 校验通过")


# ---------------------------------------------------------------------------
# dangerous
# ---------------------------------------------------------------------------


@dangerous_app.command("validate")
def dangerous_validate_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """校验 dangerous-actions.json。"""
    errors = validate_dangerous_actions(workspace)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        raise typer.Exit(code=1)
    config = load_dangerous_actions(workspace)
    print(f"dangerous-actions.json 校验通过（{len(config.rules)} 条规则）")


@dangerous_app.command("show")
def dangerous_show_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """打印当前高危操作策略。"""
    config = load_dangerous_actions(workspace)
    print(config.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# 模块入口
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
